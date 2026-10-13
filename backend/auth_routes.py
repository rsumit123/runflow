"""HTTP auth: the middleware that puts every /api request in its user's
context, and the account routes."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette._utils import get_route_path
from starlette.requests import cookie_parser

import auth
import config
import database
import tenancy

# Everything else under /api requires a session. tests/test_isolation_routes.py
# pins this set; changing it means changing that test too.
PUBLIC_ROUTES = {
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/webhook"),                 # Strava subscription check
    ("POST", "/api/webhook"),                # Strava events; routed by owner_id
    ("GET", "/api/strava/callback"),         # OAuth return; user comes from signed state
}
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

rate_limiter = auth.LoginRateLimiter()


def _header(scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return None


def _cookie(scope) -> str | None:
    """The session cookie, parsed exactly as Starlette's request.cookies does
    (the last duplicate wins), so the middleware and the handlers can never
    disagree about which session a request carries."""
    raw = _header(scope, b"cookie")
    if not raw:
        return None
    return cookie_parser(raw).get(config.SESSION_COOKIE_NAME)


def _all_session_cookies(raw_header: str | None) -> list[str]:
    """Every rf_session value the header names, duplicates included."""
    out = []
    for part in (raw_header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == config.SESSION_COOKIE_NAME and value:
            out.append(cookie_parser(f"{name}={value}")[name])
    return out


async def _send_json(send, status: int, body: dict) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


def _no_store(send):
    """Wrap send so every /api response says it must not be cached and varies by
    cookie: a signed-out browser must not replay personal data from its HTTP
    cache or bfcache. Headers the route already set are not duplicated."""
    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = list(message.get("headers") or [])
            names = {k.lower(): i for i, (k, _v) in enumerate(headers)}
            if b"cache-control" not in names:
                headers.append((b"cache-control", b"no-store"))
            if b"vary" not in names:
                headers.append((b"vary", b"Cookie"))
            elif b"cookie" not in headers[names[b"vary"]][1].lower():
                k, v = headers[names[b"vary"]]
                headers[names[b"vary"]] = (k, v + b", Cookie")
            message = {**message, "headers": headers}
        await send(message)
    return wrapped


class AuthMiddleware:
    """Resolve the session cookie and run the request as that user.

    Must sit *inside* CORSMiddleware (added before it), so a 401 still carries
    CORS headers and the browser reports the 401 rather than a CORS error.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Lifespan (and any future scope type) has no path: pass it through
        # before touching one, or startup never runs.
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        # Judge the path the router will see: uvicorn puts root_path into
        # scope["path"], Starlette routes on the path with it stripped.
        route_path = get_route_path(scope)
        if scope["type"] == "websocket" and route_path.startswith("/api/"):
            # No websocket routes exist under /api; refuse rather than guess.
            await receive()
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http" or not route_path.startswith("/api/"):
            return await self.app(scope, receive, send)
        send = _no_store(send)
        method = scope["method"]
        if method == "OPTIONS":
            return await self.app(scope, receive, send)
        origin = _header(scope, b"origin")
        if method in _MUTATING and origin is not None and origin not in config.ALLOWED_ORIGINS:
            return await _send_json(send, 403, {"detail": "Cross-origin request refused"})
        if tenancy.current_user_id() is not None:
            # Only an in-process call (call_api over ASGITransport, in the
            # caller's own task) can arrive with a user already in context. A
            # real HTTP connection always starts with none, so this cannot be
            # reached from outside.
            return await self.app(scope, receive, send)
        if (method, route_path) in PUBLIC_ROUTES:
            return await self.app(scope, receive, send)
        try:
            async with database.async_session() as s:
                uid = await auth.resolve_session(s, _cookie(scope))
        except SQLAlchemyError:
            return await _send_json(send, 503, {"detail": "Service temporarily unavailable"})
        if uid is None:
            return await _send_json(send, 401, {"detail": "Not authenticated"})
        with tenancy.user_context(uid):
            await self.app(scope, receive, send)


router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    email: str
    password: str


class SignupBody(LoginBody):
    invite_code: str


class InviteBody(BaseModel):
    uses: int = 1
    expires_days: int | None = 14


def _client_ip(request: Request) -> str:
    # SECURITY: this trusts X-Real-IP, which is only safe if the backend is
    # reachable through nginx alone (nginx sets it to $remote_addr). The deploy
    # (Task 11) must bind the container port to 127.0.0.1; published on 0.0.0.0
    # a client could send its own X-Real-IP and dodge the per-IP login limit.
    # nginx sets X-Real-IP to $remote_addr, overwriting anything the client
    # sent. X-Forwarded-For only appends, so its first entry is client-chosen
    # and would let an attacker dodge the per-IP limit.
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()[:64]
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, raw: str) -> None:
    # Host-only (no Domain): the API host is the only one that needs it, and
    # runflow.skdev.one -> runflow-api.skdev.one is same-site, so SameSite=Lax
    # cookies still travel on credentialed fetches.
    response.set_cookie(config.SESSION_COOKIE_NAME, raw,
                        max_age=config.SESSION_TTL_DAYS * 86400, httponly=True,
                        secure=config.SESSION_COOKIE_SECURE, samesite="lax", path="/")


def _user_json(user) -> dict:
    return {"id": user.id, "email": user.email, "is_admin": bool(user.is_admin)}


async def current_user():
    from models import User
    uid = tenancy.current_user_id()
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    async with database.async_session() as s:
        user = await s.get(User, uid)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


async def require_admin():
    user = await current_user()
    if not user.is_admin:
        raise HTTPException(403, "Admins only")
    return user


@router.post("/auth/signup")
async def signup(body: SignupBody, response: Response):
    async with database.async_session() as s:
        try:
            user = await auth.signup(s, body.email, body.password, body.invite_code)
            raw = await auth.create_session(s, user.id)
            await s.commit()
        except auth.AuthError as exc:
            await s.rollback()
            raise HTTPException(400, str(exc))
    _set_session_cookie(response, raw)
    return _user_json(user)


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, response: Response):
    ip = _client_ip(request)
    if rate_limiter.blocked(body.email, ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    async with database.async_session() as s:
        user = await auth.authenticate(s, body.email, body.password)
        if user is None:
            rate_limiter.record_failure(body.email, ip)
            raise HTTPException(401, "Email or password is incorrect.")
        raw = await auth.create_session(s, user.id)
        await s.commit()
    rate_limiter.record_success(body.email)
    _set_session_cookie(response, raw)
    return _user_json(user)


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    async with database.async_session() as s:
        # End every session the header names, not just the one the middleware
        # would pick, so a duplicate cookie cannot keep a session alive.
        for raw in _all_session_cookies(request.headers.get("cookie")):
            await auth.end_session(s, raw)
        await s.commit()
    # Same attributes as set_cookie: a __Host- cookie's Set-Cookie without Secure is
    # ignored by browsers, which would leave the cookie in place.
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/",
                           secure=config.SESSION_COOKIE_SECURE, httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/auth/me")
async def me():
    from models import Credential
    user = await current_user()
    async with database.async_session() as s:
        rows = (await s.execute(select(Credential.provider, Credential.status)
                                .where(Credential.user_id == user.id))).all()
    status = {p: st for p, st in rows}
    return {**_user_json(user), "strava": status.get("strava"),
            "garmin": status.get("garmin")}


@router.post("/admin/invites")
async def create_invite(body: InviteBody):
    admin = await require_admin()
    async with database.async_session() as s:
        try:
            code = await auth.create_invite(s, created_by=admin.id, uses=body.uses,
                                            expires_days=body.expires_days)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
        await s.commit()
    return {"code": code, "uses": body.uses, "expires_days": body.expires_days}


@router.get("/admin/invites")
async def list_invites():
    from models import Invite
    await require_admin()
    async with database.async_session() as s:
        rows = (await s.execute(select(Invite).order_by(Invite.created_at.desc()))).scalars().all()
    now = datetime.utcnow()
    return [{"id": r.code_hash[:8], "uses_left": r.uses_left,
             "expires_at": r.expires_at.isoformat() if r.expires_at else None,
             "expired": bool(r.expires_at and r.expires_at <= now),
             "created_at": r.created_at.isoformat()} for r in rows]
