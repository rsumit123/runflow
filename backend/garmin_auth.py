"""Per-user Garmin clients, from tokens stored encrypted in `credentials`.

Garmin offers no self-serve OAuth, so connecting uses the unofficial
garminconnect library: the password logs in once, and only the resulting
tokens are kept. The password is never stored, logged, cached, or returned.

The client cache assumes a single uvicorn worker (the Dockerfile runs exactly
one): with several, each worker would hold its own copy of a rotating token.
"""
from __future__ import annotations

import asyncio
import logging
import time

from garminconnect import Garmin, GarminConnectAuthenticationError

import credentials
import database
import tenancy

logger = logging.getLogger(__name__)

MFA_TTL_SEC = 300
# login() treats a token string of 512 chars or fewer as a directory path.
MIN_TOKEN_LEN = 513
_clients: dict[int, Garmin] = {}
_client_locks: dict[int, asyncio.Lock] = {}
# user_id -> the token string the stored row held when we last read or wrote it.
# Stored credentials are only touched while they still carry this value: a
# disconnect (row gone) or reconnect (new tokens) in between must win.
_saved_tokens: dict[int, str] = {}
# user_id -> (half-logged-in Garmin, client_state, monotonic start time)
_pending_mfa: dict[int, tuple[Garmin, object, float]] = {}


class GarminNotConnected(RuntimeError):
    """The user in context has no usable Garmin connection."""


class GarminLoginFailed(RuntimeError):
    """Connecting failed. The message is safe to show the user."""


def reset() -> None:
    _clients.clear()
    _client_locks.clear()
    _saved_tokens.clear()
    _pending_mfa.clear()


def _lock(uid: int) -> asyncio.Lock:
    return _client_locks.setdefault(uid, asyncio.Lock())


def _restore(tokens: str) -> Garmin:
    g = Garmin()
    g.login(tokens)
    return g


async def _stored_tokens(uid: int) -> tuple[str | None, str | None]:
    """(tokens, status) of the stored credential, or (None, None)."""
    async with database.async_session() as s:
        cred = await credentials.get(s, uid, "garmin")
    if cred is None:
        return None, None
    return cred.data.get("tokens"), cred.status


async def get_garmin() -> Garmin:
    uid = tenancy.current_user_id()
    if uid is None:
        raise tenancy.NoUserContext("Garmin needs a user in context")
    if uid in _clients:
        return _clients[uid]
    # Two cold requests must not each build (and each refresh with) their own client.
    async with _lock(uid):
        if uid in _clients:
            return _clients[uid]
        tokens, status = await _stored_tokens(uid)
        if tokens is None or status != "ok":
            raise GarminNotConnected("Garmin is not connected")
        if not isinstance(tokens, str) or len(tokens) < MIN_TOKEN_LEN:
            raise GarminNotConnected("stored Garmin tokens are malformed")
        try:
            g = await asyncio.to_thread(_restore, tokens)
        except GarminConnectAuthenticationError:
            logger.warning("Garmin tokens rejected for user %s", uid)
            await _mark_needs_reconnect(uid, tokens)
            raise GarminNotConnected("Garmin needs reconnecting") from None
        # Anything else (network, rate limit) is transient: do not disconnect the user.
        _clients[uid] = g
        _saved_tokens[uid] = tokens
        await _save_if_rotated(uid, g)
        return g


async def _mark_needs_reconnect(uid: int, held: str) -> None:
    """Flag the connection, but only if it still holds the tokens that failed."""
    async with database.async_session() as s:
        cred = await credentials.get(s, uid, "garmin")
        if cred is not None and cred.data.get("tokens") == held:
            await credentials.set_status(s, uid, "garmin", "needs_reconnect")
            await s.commit()


async def _save_if_rotated(uid: int, g: Garmin) -> None:
    """Caller holds the user's lock, or is building the client."""
    current = g.client.dumps()
    held = _saved_tokens.get(uid)
    if held is None or current == held:
        return
    async with database.async_session() as s:
        cred = await credentials.get(s, uid, "garmin")
        if cred is None or cred.data.get("tokens") != held:
            return  # disconnected or reconnected meanwhile: these tokens are stale
        await credentials.put(s, uid, "garmin", {"tokens": current},
                              status=cred.status)
        await s.commit()
    _saved_tokens[uid] = current


async def save_if_rotated(uid: int) -> None:
    """The library refreshes its session in place; keep the stored copy current."""
    g = _clients.get(uid)
    if g is None:
        return
    async with _lock(uid):
        if _clients.get(uid) is g:
            await _save_if_rotated(uid, g)


async def _reject(uid: int, g: Garmin) -> None:
    """Garmin refused this client's tokens mid-use."""
    async with _lock(uid):
        held = _saved_tokens.get(uid)
        if _clients.get(uid) is not g:
            return  # already replaced (reconnect) or dropped
        if held is not None:
            await _mark_needs_reconnect(uid, held)
        _clients.pop(uid, None)
        _saved_tokens.pop(uid, None)


async def run(g: Garmin, name: str, *args):
    """Call a (blocking) client method off the event loop for the user in context.

    Writes back tokens the library rotated, and turns a rejection into
    needs_reconnect plus GarminNotConnected.
    """
    uid = tenancy.current_user_id()
    try:
        result = await asyncio.to_thread(getattr(g, name), *args)
    except GarminConnectAuthenticationError:
        logger.warning("Garmin rejected the tokens of user %s", uid)
        if uid is not None:
            await _reject(uid, g)
        raise GarminNotConnected("Garmin needs reconnecting") from None
    if uid is not None and _clients.get(uid) is g:
        await save_if_rotated(uid)
    return result


async def _store(user_id: int, g: Garmin) -> None:
    tokens = g.client.dumps()
    g.password = g.username = None
    if not isinstance(tokens, str) or len(tokens) < MIN_TOKEN_LEN:
        raise GarminLoginFailed("Garmin returned an unexpected session. Try again.")
    async with _lock(user_id):
        async with database.async_session() as s:
            await credentials.put(s, user_id, "garmin", {"tokens": tokens})
            await s.commit()
        # The logged-in object is discarded; the next get_garmin() builds a
        # clean client from the stored tokens.
        _clients.pop(user_id, None)
        _saved_tokens.pop(user_id, None)


def _sweep_pending() -> None:
    now = time.monotonic()
    for uid in [u for u, e in _pending_mfa.items() if now - e[2] > MFA_TTL_SEC]:
        g = _pending_mfa.pop(uid)[0]
        g.password = g.username = None


async def start_login(user_id: int, email: str, password: str) -> str:
    """'connected' or 'needs_mfa'. Raises GarminLoginFailed with a safe message."""
    _sweep_pending()
    old = _pending_mfa.pop(user_id, None)
    if old is not None:
        old[0].password = old[0].username = None

    def _go():
        g = Garmin(email=email, password=password, return_on_mfa=True)
        try:
            return g, g.login()
        finally:
            g.password = None  # the library keeps it on the object

    try:
        g, result = await asyncio.to_thread(_go)
    except Exception:  # noqa: BLE001 - never chain: messages may echo inputs
        raise GarminLoginFailed("Garmin rejected those credentials.") from None
    # Real library: ("needs_mfa", None) when MFA is required, (None, None) on
    # success. The MFA state lives on the Garmin object, so keep that object.
    status, client_state = result if isinstance(result, tuple) else (None, None)
    if status == "needs_mfa":
        _pending_mfa[user_id] = (g, client_state, time.monotonic())
        return "needs_mfa"
    await _store(user_id, g)
    return "connected"


async def complete_mfa(user_id: int, code: str) -> str:
    _sweep_pending()
    entry = _pending_mfa.pop(user_id, None)
    if entry is None:
        raise GarminLoginFailed("That sign-in expired. Start again.")
    g, client_state, _ = entry
    try:
        await asyncio.to_thread(g.resume_login, client_state, code)
    except Exception:  # noqa: BLE001
        g.password = g.username = None
        raise GarminLoginFailed("Garmin rejected that code. Start again.") from None
    await _store(user_id, g)
    return "connected"


async def disconnect(user_id: int) -> None:
    async with _lock(user_id):
        async with database.async_session() as s:
            await credentials.delete(s, user_id, "garmin")
            await s.commit()
        _clients.pop(user_id, None)
        _saved_tokens.pop(user_id, None)
    old = _pending_mfa.pop(user_id, None)
    if old is not None:
        old[0].password = old[0].username = None
