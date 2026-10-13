"""Per-user Claude connector URLs. The secret in the URL is the credential.

Claude's custom connectors support only authless or OAuth servers, so, as
before, the secret lives in the path. Each user now has their own, and it
decides whose data every tool sees.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

import auth
import config
import database
import tenancy


async def user_for_token(raw: str | None) -> int | None:
    if not raw:
        return None
    from models import ConnectorToken
    async with database.async_session() as s:
        return (await s.execute(select(ConnectorToken.user_id).where(
            ConnectorToken.token_hash == auth.hash_token(raw)))).scalar_one_or_none()


async def set_token(session, user_id: int, raw: str) -> None:
    """Install a specific token. Used to carry the old MCP_SECRET over."""
    from models import ConnectorToken
    await session.execute(delete(ConnectorToken).where(ConnectorToken.user_id == user_id))
    session.add(ConnectorToken(token_hash=auth.hash_token(raw), user_id=user_id,
                               created_at=datetime.utcnow()))
    await session.flush()


async def rotate(session, user_id: int) -> str:
    raw = auth.new_token()
    await set_token(session, user_id, raw)
    return raw


async def exists(session, user_id: int) -> bool:
    from models import ConnectorToken
    return (await session.execute(select(ConnectorToken.user_id).where(
        ConnectorToken.user_id == user_id))).scalar_one_or_none() is not None


def url_for(raw: str) -> str:
    return f"{config.PUBLIC_API_URL}/mcp/{raw}/mcp"


class ConnectorApp:
    """Mounted at /mcp/{token}. Unknown token: 404, as before. Known: run as its owner."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await receive()
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        try:
            uid = await user_for_token((scope.get("path_params") or {}).get("token"))
        except SQLAlchemyError:
            return await _plain(send, 503, b"Service temporarily unavailable")
        if uid is None:
            return await _plain(send, 404, b"Not Found")
        if scope.get("method") == "GET":
            # Stateless server: there is no SSE stream to offer, which the
            # streamable-HTTP spec allows us to say with 405 (not hang).
            return await _plain(send, 405, b"Method Not Allowed",
                                [(b"allow", b"POST")])
        with tenancy.user_context(uid):
            await self.inner(scope, receive, send)


async def _plain(send, status: int, body: bytes, extra=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), *extra]})
    await send({"type": "http.response.body", "body": body})
