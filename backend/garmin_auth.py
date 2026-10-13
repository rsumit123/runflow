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
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from garminconnect import (Garmin, GarminConnectAuthenticationError,
                           GarminConnectConnectionError)

import credentials
import database
import tenancy

logger = logging.getLogger(__name__)

MFA_TTL_SEC = 300
MFA_MAX_ATTEMPTS = 3
# Sign-in attempts. Each one can hold a thread for a minute (the library retries
# with sleeps on 429), and Garmin or Cloudflare will block our IP if we hammer it.
CONNECT_PER_USER_PER_HOUR = 5
CONNECT_GLOBAL_PER_HOUR = 30
CONNECT_WINDOW_SEC = 3600
MAX_CONCURRENT_LOGINS = 2
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
_mfa_attempts: dict[int, int] = {}
_connect_times: dict[int, deque] = {}
_connect_global: deque = deque()
# Logins run in their own small pool so they cannot starve the default thread
# pool that every other request (and run()) uses.
_login_pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LOGINS,
                                 thread_name_prefix="garmin-login")
_login_sem: tuple[object, asyncio.Semaphore] | None = None


def _semaphore() -> asyncio.Semaphore:
    global _login_sem
    loop = asyncio.get_running_loop()
    if _login_sem is None or _login_sem[0] is not loop:
        _login_sem = (loop, asyncio.Semaphore(MAX_CONCURRENT_LOGINS))
    return _login_sem[1]


class GarminAccessError(RuntimeError):
    """Garmin cannot be used right now for this user (base of the two below)."""


class GarminNotConnected(GarminAccessError):
    """The user in context has no usable Garmin connection."""


class GarminUnavailable(GarminAccessError):
    """Garmin could not be reached, or failed for a reason that is not a token
    rejection. The stored connection is left alone: retrying later may work."""


class GarminRateLimited(RuntimeError):
    """Too many sign-in attempts."""


class GarminLoginFailed(RuntimeError):
    """Connecting failed. The message is safe to show the user."""


def reset() -> None:
    _clients.clear()
    _client_locks.clear()
    _saved_tokens.clear()
    for g, _, _ in _pending_mfa.values():
        _scrub(g)
    _pending_mfa.clear()
    _mfa_attempts.clear()
    _connect_times.clear()
    _connect_global.clear()


def _check_connect_rate(uid: int) -> None:
    """Count this attempt, or raise before Garmin is touched."""
    now = time.monotonic()
    mine = _connect_times.setdefault(uid, deque())
    for q in (mine, _connect_global):
        while q and now - q[0] > CONNECT_WINDOW_SEC:
            q.popleft()
    if len(mine) >= CONNECT_PER_USER_PER_HOUR or len(_connect_global) >= CONNECT_GLOBAL_PER_HOUR:
        raise GarminRateLimited("Too many Garmin sign-in attempts; try again later")
    mine.append(now)
    _connect_global.append(now)


def _strip_request_body(resp) -> None:
    """Forget what a response's request sent (it holds password=...), keep the page."""
    for r in [resp, *list(getattr(resp, "history", None) or [])]:
        req = getattr(r, "request", None)
        if req is not None and getattr(req, "body", None) is not None:
            try:
                req.body = None
            except Exception:  # noqa: BLE001
                pass


def _scrub(g, secret: str | None = None, final: bool = True) -> None:
    """Drop every copy of the password we can find on a Garmin object.

    The widget login strategy keeps its last response on the Client, and that
    response's request body holds password=... . While an MFA step is pending
    (final=False) the widget flow still NEEDS that response (it reads the CSRF
    token from its text on resume), so only its request body is blanked. Once
    the login is over (final=True) every cached response is removed outright.
    Anything else whose repr still shows `secret` is removed in both modes.
    """
    try:
        g.password = g.username = None
        d = getattr(getattr(g, "client", None), "__dict__", None)
        if d is None:
            return
        import requests
        kinds: tuple = (requests.Response, requests.PreparedRequest)
        try:
            from curl_cffi import requests as cr
            kinds += (cr.Response,)
        except Exception:  # noqa: BLE001
            pass
        for name, val in list(d.items()):
            if name == "_widget_last_resp" or isinstance(val, kinds):
                if final:
                    d.pop(name, None)
                    continue
                _strip_request_body(val)
            if secret and secret in repr(val):
                d.pop(name, None)
    except Exception:  # noqa: BLE001 - scrubbing must never break a login
        logger.warning("Could not fully scrub a Garmin login object")


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _definitive_rejection(exc: BaseException, codes: str = "401|403") -> bool:
    """True only when Garmin itself refused the tokens.

    garminconnect 0.3.6 hides the difference: Garmin.login(tokens) and
    _load_profile_and_settings raise GarminConnectAuthenticationError for a
    refused token AND for a plain network failure (after 3 retries). What tells
    them apart is the chain:
      - a refused token surfaces from Client._run_request as
        GarminConnectConnectionError("API Error 401 - ...") (after its own refresh
        attempt also failed), chained under the auth error;
      - a network failure chains a requests/curl ConnectionError instead;
      - tokens that cannot even be parsed make login() fall through to
        "Username and password are required", with no HTTP involved.
    """
    pattern = re.compile(rf"API Error ({codes})\b")
    for e in _chain(exc):
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status is not None and str(status) in codes.split("|"):
            return True
        if isinstance(e, GarminConnectConnectionError) and pattern.search(str(e)):
            return True
    return "Username and password are required" in str(exc)


def _lock(uid: int) -> asyncio.Lock:
    return _client_locks.setdefault(uid, asyncio.Lock())


class _TokensRejected(Exception):
    """Garmin's token endpoint refused these tokens: only a new sign-in helps."""


def refresh_outcome(g) -> str:
    """Ask Garmin's refresh endpoint itself: 'ok', 'rejected' or 'unavailable'.

    The library calls Client._refresh_di_token on a 401 but swallows every failure
    at debug level (_refresh_session), so "access token expired and the refresh
    endpoint is down" looks exactly like "token revoked". Calling it directly gives
    the real answer. Client._refresh_di_token signals:
      - no refresh token held:      GarminConnectAuthenticationError("No DI refresh token available")
      - endpoint answered not-ok:   GarminConnectAuthenticationError(f"DI token refresh failed: {status} ...")
      - transport failure:          the raw requests/curl_cffi exception
      - success:                    returns, tokens updated in place
    Only 400/401/403 (invalid_grant and friends) mean the refresh token is dead;
    429, 5xx and connection errors say nothing about it.
    """
    try:
        g.client._refresh_di_token()
        return "ok"
    except GarminConnectAuthenticationError as exc:
        msg = str(exc)
        if "No DI refresh token available" in msg:
            return "rejected"
        m = re.search(r"DI token refresh failed: (\d{3})", msg)
        if m and m.group(1) in ("400", "401", "403"):
            return "rejected"
        return "unavailable"
    except Exception:  # noqa: BLE001
        return "unavailable"


def _restore(tokens: str) -> Garmin:
    g = Garmin()
    try:
        g.login(tokens)
        return g
    except GarminConnectAuthenticationError as exc:
        if "Username and password are required" in str(exc):
            raise _TokensRejected() from None      # tokens cannot even be parsed
        if not _definitive_rejection(exc):
            raise                                  # no HTTP 401/403 behind it: an outage
    # The API answered 401/403. That alone does not say the refresh token is dead.
    outcome = refresh_outcome(g)
    if outcome == "rejected":
        raise _TokensRejected()
    if outcome == "unavailable":
        raise GarminUnavailable("Garmin is unreachable right now")
    # Refreshed: the access token had merely expired. Load the profile once more.
    try:
        g._load_profile_and_settings()
    except GarminConnectAuthenticationError as exc:
        if _definitive_rejection(exc):
            raise _TokensRejected() from None
        raise
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
        except _TokensRejected:
            logger.warning("Garmin tokens rejected for user %s", uid)
            await _mark_needs_reconnect(uid, tokens)
            raise GarminNotConnected("Garmin needs reconnecting") from None
        except Exception as exc:  # noqa: BLE001
            # Outage, rate limit, or anything we cannot prove is a rejection: the
            # stored connection stays as it is, and the user retries shortly.
            logger.warning("Garmin unavailable for user %s: %s", uid, type(exc).__name__)
            raise GarminUnavailable("Garmin is unreachable right now") from None
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

    Writes back tokens the library rotated (even if the call then fails). A 401 is
    classified by asking the refresh endpoint why: refused means needs_reconnect +
    GarminNotConnected, working means the call is retried once, anything else is
    GarminUnavailable and the stored connection is left alone.
    """
    uid = tenancy.current_user_id()
    try:
        try:
            return await asyncio.to_thread(getattr(g, name), *args)
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            if not _definitive_rejection(exc, codes="401"):
                if isinstance(exc, GarminConnectAuthenticationError):
                    raise GarminUnavailable("Garmin is unreachable right now") from None
                raise
            outcome = await asyncio.to_thread(refresh_outcome, g)
            if outcome == "unavailable":
                raise GarminUnavailable("Garmin is unreachable right now") from None
            if outcome == "ok":
                try:
                    return await asyncio.to_thread(getattr(g, name), *args)
                except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc2:
                    if not _definitive_rejection(exc2, codes="401"):
                        raise
            logger.warning("Garmin rejected the tokens of user %s", uid)
            if uid is not None:
                await _reject(uid, g)
            raise GarminNotConnected("Garmin needs reconnecting") from None
    finally:
        if uid is not None and _clients.get(uid) is g:
            try:
                await save_if_rotated(uid)
            except Exception:  # noqa: BLE001 - must not mask the call's own outcome
                logger.warning("Could not save rotated Garmin tokens for user %s", uid)


async def _store(user_id: int, g: Garmin) -> None:
    tokens = g.client.dumps()
    _scrub(g, final=True)
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


def _drop_pending(uid: int) -> None:
    entry = _pending_mfa.pop(uid, None)
    _mfa_attempts.pop(uid, None)
    if entry is not None:
        _scrub(entry[0])


def _sweep_pending() -> None:
    now = time.monotonic()
    for uid in [u for u, e in _pending_mfa.items() if now - e[2] > MFA_TTL_SEC]:
        _drop_pending(uid)


async def start_login(user_id: int, email: str, password: str) -> str:
    """'connected' or 'needs_mfa'. Raises GarminLoginFailed with a safe message,
    or GarminRateLimited before Garmin is touched."""
    _check_connect_rate(user_id)
    _sweep_pending()
    _drop_pending(user_id)

    def _go():
        g = Garmin(email=email, password=password, return_on_mfa=True)
        try:
            return g, g.login()
        finally:
            # The library keeps the password and the login responses. If MFA is
            # pending the client still needs its last page, minus the request body.
            _scrub(g, password, final=False)

    try:
        async with _semaphore():
            g, result = await asyncio.get_running_loop().run_in_executor(_login_pool, _go)
    except Exception:  # noqa: BLE001 - never chain: messages may echo inputs
        raise GarminLoginFailed("Garmin rejected those credentials.") from None
    # Real library: ("needs_mfa", None) when MFA is required, (None, None) on
    # success. The MFA state lives on the Garmin object, so keep that object.
    status, client_state = result if isinstance(result, tuple) else (None, None)
    if status == "needs_mfa":
        _pending_mfa[user_id] = (g, client_state, time.monotonic())
        _mfa_attempts[user_id] = 0
        return "needs_mfa"
    await _store(user_id, g)
    return "connected"


async def complete_mfa(user_id: int, code: str) -> str:
    _sweep_pending()
    entry = _pending_mfa.get(user_id)
    if entry is None:
        raise GarminLoginFailed("That sign-in expired. Start again.")
    g, client_state, _ = entry
    try:
        async with _semaphore():
            await asyncio.get_running_loop().run_in_executor(
                _login_pool, g.resume_login, client_state, code)
    except Exception:  # noqa: BLE001
        _mfa_attempts[user_id] = _mfa_attempts.get(user_id, 0) + 1
        if _mfa_attempts[user_id] >= MFA_MAX_ATTEMPTS:
            _drop_pending(user_id)
            raise GarminLoginFailed("Garmin rejected that code too many times. Start again.") from None
        raise GarminLoginFailed("Garmin rejected that code. Check it and try again.") from None
    _pending_mfa.pop(user_id, None)
    _mfa_attempts.pop(user_id, None)
    await _store(user_id, g)
    return "connected"


async def disconnect(user_id: int) -> None:
    async with _lock(user_id):
        async with database.async_session() as s:
            await credentials.delete(s, user_id, "garmin")
            await s.commit()
        _clients.pop(user_id, None)
        _saved_tokens.pop(user_id, None)
    _drop_pending(user_id)


def error_status(exc: BaseException) -> int | None:
    """The HTTP status Garmin answered with, from an exception or its chain."""
    for e in _chain(exc):
        status = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
        m = re.search(r"API Error (\d{3})\b", str(e))
        if m:
            return int(m.group(1))
    return None
