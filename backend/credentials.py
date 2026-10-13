"""Per-user provider credentials (Strava, Garmin), encrypted at rest.

Keyed explicitly by user id rather than through the tenancy filter, because
webhook routing has to find a user before any user is in context.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.exc import IntegrityError

import config


class AlreadyLinked(Exception):
    """This provider account is already linked to a different RunFlow user."""


@dataclass
class Cred:
    data: dict
    external_id: str | None
    status: str


def _fernet() -> Fernet:
    if not config.CREDENTIALS_KEY:
        raise RuntimeError("CREDENTIALS_KEY is not set; refusing to store or read credentials")
    return Fernet(config.CREDENTIALS_KEY.encode())


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(blob: str) -> dict:
    return json.loads(_fernet().decrypt(blob.encode()).decode())


async def put(session, user_id: int, provider: str, data: dict,
              external_id: str | None = None, status: str = "ok") -> None:
    from models import Credential
    blob = encrypt(data)
    if external_id is not None:
        owner = await user_for_external(session, provider, external_id)
        if owner is not None and owner != user_id:
            raise AlreadyLinked(f"That {provider} account is already linked to another user.")
    try:
        async with session.begin_nested():
            row = await session.get(Credential, (user_id, provider))
            if row is None:
                row = Credential(user_id=user_id, provider=provider)
                session.add(row)
            row.secret_blob = blob
            if external_id is not None:
                row.external_id = str(external_id)
            row.status = status
            row.updated_at = datetime.utcnow()
            await session.flush()
    except IntegrityError:
        if external_id is None:
            raise
        # Lost a race: another user linked this account after the check above.
        raise AlreadyLinked(f"That {provider} account is already linked to another user.") from None


async def get(session, user_id: int, provider: str) -> Cred | None:
    from models import Credential
    row = await session.get(Credential, (user_id, provider))
    if row is None:
        return None
    return Cred(decrypt(row.secret_blob), row.external_id, row.status)


async def set_status(session, user_id: int, provider: str, status: str) -> None:
    from models import Credential
    row = await session.get(Credential, (user_id, provider))
    if row is not None:
        row.status = status
        row.updated_at = datetime.utcnow()
        await session.flush()


async def delete(session, user_id: int, provider: str) -> None:
    from models import Credential
    await session.execute(sa_delete(Credential).where(
        Credential.user_id == user_id, Credential.provider == provider))


async def user_for_external(session, provider: str, external_id) -> int | None:
    from models import Credential
    return (await session.execute(select(Credential.user_id).where(
        Credential.provider == provider,
        Credential.external_id == str(external_id),
    ))).scalar_one_or_none()


async def users_with(session, provider: str) -> list[int]:
    """Users whose credential for `provider` is usable (status ok)."""
    from models import Credential
    return list((await session.execute(select(Credential.user_id).where(
        Credential.provider == provider, Credential.status == "ok",
    ).order_by(Credential.user_id))).scalars().all())
