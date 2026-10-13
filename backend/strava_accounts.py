"""Per-user Strava: OAuth connect, token storage, and one client per user."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx

import config
import credentials
import database
import tenancy
from strava_client import StravaClient, StravaNotConnected  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)

STATE_TTL_SEC = 600
NONCE_COOKIE = "strava_oauth"
# One client per user, cached in this process. That is only correct with a single
# uvicorn worker (the Dockerfile CMD runs exactly one): with several, each worker
# would hold its own copy of a rotating refresh token and they would burn each
# other's. Moving to more workers needs this cache and its locks replaced.
_clients: dict[int, StravaClient] = {}
_client_locks: dict[int, asyncio.Lock] = {}


class StravaConnectError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # short code for the redirect: capacity | exchange_failed


def _secret() -> bytes:
    if not config.SESSION_STATE_SECRET:
        raise RuntimeError("SESSION_STATE_SECRET is not set")
    return config.SESSION_STATE_SECRET.encode()


def _b(text: str) -> bytes:
    # State arrives from the URL: it may hold any character, so never let encoding raise.
    return text.encode("utf-8", "surrogatepass")


def _sig(payload: str) -> str:
    return hmac.new(_secret(), _b(payload), hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_urlsafe(16)


def sign_state(user_id: int, now: float | None = None, nonce: str | None = None) -> str:
    exp = int((now if now is not None else time.time()) + STATE_TTL_SEC)
    payload = f"{user_id}.{exp}.{nonce or new_nonce()}"
    return f"{payload}.{_sig(payload)}"


def parse_state(state: str, now: float | None = None) -> tuple[int, str] | None:
    """(user_id, nonce) for a state we signed and that has not expired, else None."""
    parts = (state or "").split(".")
    if len(parts) != 4:
        return None
    uid, exp, nonce, sig = parts
    if not hmac.compare_digest(_b(sig), _b(_sig(f"{uid}.{exp}.{nonce}"))):
        return None
    try:
        if int(exp) < (now if now is not None else time.time()):
            return None
        return int(uid), nonce
    except ValueError:
        return None


def verify_state(state: str, now: float | None = None) -> int | None:
    parsed = parse_state(state, now)
    return parsed[0] if parsed else None


def nonce_matches(cookie_value: str | None, nonce: str) -> bool:
    return bool(cookie_value) and hmac.compare_digest(_b(cookie_value), _b(nonce))


def authorize_url(user_id: int, nonce: str) -> str:
    params = {"client_id": config.STRAVA_CLIENT_ID,
              "redirect_uri": config.STRAVA_REDIRECT_URI,
              "response_type": "code", "approval_prompt": "auto",
              "scope": "read,activity:read_all", "state": sign_state(user_id, nonce=nonce)}
    return f"{config.STRAVA_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(config.STRAVA_TOKEN_URL, data={
                "client_id": config.STRAVA_CLIENT_ID,
                "client_secret": config.STRAVA_CLIENT_SECRET,
                "code": code, "grant_type": "authorization_code"})
    except httpx.HTTPError as exc:
        logger.warning("Strava token exchange unreachable: %s", type(exc).__name__)
        raise StravaConnectError("exchange_failed") from None
    if r.status_code != 200:
        # Strava refuses a new athlete past the app's athlete capacity. A 429 is
        # the rate limiter, whose message also says "limit", and is not that.
        capacity = r.status_code != 429 and "limit" in r.text.lower()
        reason = "capacity" if capacity else "exchange_failed"
        logger.warning("Strava token exchange failed (%s): %s", r.status_code, reason)
        raise StravaConnectError(reason)
    try:
        data = r.json()
        data["access_token"], data["refresh_token"]
    except (ValueError, KeyError, TypeError):
        raise StravaConnectError("exchange_failed") from None
    return data


async def save_tokens(user_id: int, data: dict) -> None:
    try:
        athlete_id = (data.get("athlete") or {}).get("id")
        tokens = {"access_token": data["access_token"],
                  "refresh_token": data["refresh_token"],
                  "expires_at": data.get("expires_at")}
    except (KeyError, AttributeError, TypeError):
        raise StravaConnectError("exchange_failed") from None
    async with database.async_session() as s:
        await credentials.put(s, user_id, "strava", tokens,
                              external_id=str(athlete_id) if athlete_id else None)
        await s.commit()
    await _drop(user_id)


async def client_for(user_id: int | None) -> StravaClient:
    if user_id is None:
        raise tenancy.NoUserContext("Strava needs a user in context")
    if user_id in _clients:
        return _clients[user_id]
    # Two cold requests must not each build (and each refresh with) their own client.
    lock = _client_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        if user_id in _clients:
            return _clients[user_id]
        async with database.async_session() as s:
            cred = await credentials.get(s, user_id, "strava")
        if cred is None or cred.status != "ok":
            raise StravaNotConnected("Strava is not connected")

        async def on_refresh(tokens: dict) -> None:
            # Update only. If the user disconnected while the request was in
            # flight, the row is gone and these tokens are dropped, not resurrected.
            async with database.async_session() as s:
                if await credentials.get(s, user_id, "strava") is None:
                    return
                await credentials.put(s, user_id, "strava", tokens)
                await s.commit()

        async def on_invalid() -> None:
            async with database.async_session() as s:
                stored = await credentials.get(s, user_id, "strava")
                # Only if it is still this grant: a reconnect may have replaced it.
                if stored is not None and stored.data.get("refresh_token") == client.refresh_token:
                    await credentials.set_status(s, user_id, "strava", "needs_reconnect")
                    await s.commit()
            if _clients.get(user_id) is client:
                await _drop(user_id)

        client = StravaClient(access_token=cred.data.get("access_token", ""),
                              refresh_token=cred.data.get("refresh_token", ""),
                              on_refresh=on_refresh, on_invalid=on_invalid)
        _clients[user_id] = client
        return client


async def token_revoked(user_id: int) -> bool:
    """True only if Strava itself says this user's grant is gone.

    A webhook says "deauthorized" on the strength of a public athlete id, so it
    is never believed on its own. The user's stored token is tried against
    Strava (refreshing if it merely expired); only a rejected refresh counts.
    Any other trouble means "not proven", i.e. False.
    """
    try:
        c = await client_for(user_id)
        await c.get_athlete()
        return False
    except StravaNotConnected:
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not confirm Strava revocation for user %s: %s",
                    user_id, type(exc).__name__)
        return False


async def forget(user_id: int) -> None:
    """Delete the stored connection without calling Strava (e.g. after deauthorize)."""
    async with database.async_session() as s:
        await credentials.delete(s, user_id, "strava")
        await s.commit()
    await _drop(user_id)


async def disconnect(user_id: int) -> None:
    """Forget the connection, then revoke at Strava (best effort)."""
    async with database.async_session() as s:
        cred = await credentials.get(s, user_id, "strava")
    await forget(user_id)
    if cred is not None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post("https://www.strava.com/oauth/deauthorize",
                             data={"access_token": cred.data.get("access_token", "")})
        except Exception as exc:  # noqa: BLE001 - revocation is best effort
            logger.info("Strava deauthorize failed for user %s: %s", user_id, exc)


async def _drop(user_id: int) -> None:
    c = _clients.pop(user_id, None)
    if c is not None:
        await c.close()


async def close_all() -> None:
    for uid in list(_clients):
        await _drop(uid)
