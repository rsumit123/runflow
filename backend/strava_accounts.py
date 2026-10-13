"""Per-user Strava: OAuth connect, token storage, and one client per user."""
from __future__ import annotations

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
from strava_client import StravaClient

logger = logging.getLogger(__name__)

STATE_TTL_SEC = 600
_clients: dict[int, StravaClient] = {}


class StravaNotConnected(RuntimeError):
    """The user in context has no usable Strava connection."""


class StravaConnectError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # short code for the redirect: capacity | exchange_failed


def _secret() -> bytes:
    if not config.SESSION_STATE_SECRET:
        raise RuntimeError("SESSION_STATE_SECRET is not set")
    return config.SESSION_STATE_SECRET.encode()


def sign_state(user_id: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + STATE_TTL_SEC)
    payload = f"{user_id}.{exp}.{secrets.token_urlsafe(8)}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_state(state: str, now: float | None = None) -> int | None:
    parts = (state or "").split(".")
    if len(parts) != 4:
        return None
    uid, exp, nonce, sig = parts
    good = hmac.new(_secret(), f"{uid}.{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        if int(exp) < (now if now is not None else time.time()):
            return None
        return int(uid)
    except ValueError:
        return None


def authorize_url(user_id: int) -> str:
    params = {"client_id": config.STRAVA_CLIENT_ID,
              "redirect_uri": config.STRAVA_REDIRECT_URI,
              "response_type": "code", "approval_prompt": "auto",
              "scope": "read,activity:read_all", "state": sign_state(user_id)}
    return f"{config.STRAVA_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(config.STRAVA_TOKEN_URL, data={
            "client_id": config.STRAVA_CLIENT_ID,
            "client_secret": config.STRAVA_CLIENT_SECRET,
            "code": code, "grant_type": "authorization_code"})
    if r.status_code != 200:
        # Strava refuses a new athlete past the app's athlete capacity.
        reason = "capacity" if "limit" in r.text.lower() else "exchange_failed"
        logger.warning("Strava token exchange failed (%s): %s", r.status_code, reason)
        raise StravaConnectError(reason)
    return r.json()


async def save_tokens(user_id: int, data: dict) -> None:
    athlete_id = (data.get("athlete") or {}).get("id")
    async with database.async_session() as s:
        await credentials.put(s, user_id, "strava",
                              {"access_token": data["access_token"],
                               "refresh_token": data["refresh_token"],
                               "expires_at": data.get("expires_at")},
                              external_id=str(athlete_id) if athlete_id else None)
        await s.commit()
    await _drop(user_id)


async def client_for(user_id: int | None) -> StravaClient:
    if user_id is None:
        raise tenancy.NoUserContext("Strava needs a user in context")
    if user_id in _clients:
        return _clients[user_id]
    async with database.async_session() as s:
        cred = await credentials.get(s, user_id, "strava")
    if cred is None or cred.status != "ok":
        raise StravaNotConnected("Strava is not connected")

    async def on_refresh(tokens: dict) -> None:
        async with database.async_session() as s:
            await credentials.put(s, user_id, "strava", tokens)
            await s.commit()

    client = StravaClient(access_token=cred.data.get("access_token", ""),
                          refresh_token=cred.data.get("refresh_token", ""),
                          on_refresh=on_refresh)
    _clients[user_id] = client
    return client


async def forget(user_id: int) -> None:
    """Delete the stored connection without calling Strava (e.g. after deauthorize)."""
    async with database.async_session() as s:
        await credentials.delete(s, user_id, "strava")
        await s.commit()
    await _drop(user_id)


async def disconnect(user_id: int) -> None:
    """Revoke at Strava (best effort), then forget."""
    async with database.async_session() as s:
        cred = await credentials.get(s, user_id, "strava")
    if cred is not None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                await c.post("https://www.strava.com/oauth/deauthorize",
                             data={"access_token": cred.data.get("access_token", "")})
        except Exception as exc:  # noqa: BLE001 - revocation is best effort
            logger.info("Strava deauthorize failed for user %s: %s", user_id, exc)
    await forget(user_id)


async def _drop(user_id: int) -> None:
    c = _clients.pop(user_id, None)
    if c is not None:
        await c.close()


async def close_all() -> None:
    for uid in list(_clients):
        await _drop(uid)
