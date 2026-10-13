"""Account administration from the server shell.

  python admin.py set-password EMAIL        prompts twice; never an argument
  python admin.py create-invite [--uses N] [--expires-days D]
  python admin.py list-users
  python admin.py delete-user EMAIL --yes   removes the user and all their data
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import delete, func, select

import auth
import database
import tenancy


async def set_password(email: str, password: str) -> None:
    auth.check_password_policy(password)
    async with database.async_session() as s:
        user = await auth.get_user_by_email(s, email)
        if user is None:
            raise SystemExit(f"no user {email}")
        user.password_hash = auth.hash_password(password)
        # A password change signs out every existing session.
        from models import AuthSession
        await s.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
        await s.commit()


async def create_invite(uses: int, expires_days: int | None) -> str:
    async with database.async_session() as s:
        code = await auth.create_invite(s, created_by=None, uses=uses,
                                        expires_days=expires_days)
        await s.commit()
    return code


async def list_users() -> list[tuple]:
    from models import User
    async with database.async_session() as s:
        return (await s.execute(select(User.id, User.email, User.is_admin,
                                       User.created_at).order_by(User.id))).all()


async def delete_user(email: str) -> None:
    import models
    from models import AuthSession, ConnectorToken, Credential, User
    async with database.async_session() as s:
        user = await auth.get_user_by_email(s, email)
        if user is None:
            raise SystemExit(f"no user {email}")
        if user.is_admin:
            admins = (await s.execute(select(func.count()).select_from(User)
                                      .where(User.is_admin.is_(True)))).scalar_one()
            if admins <= 1:
                raise SystemExit("refusing to delete the last admin")
        uid = user.id
    tenant_models = [m for m in models.Base.registry.mappers
                     if issubclass(m.class_, tenancy.TenantMixin)]
    with tenancy.system_scope():
        async with database.async_session() as s:
            for mapper in tenant_models:
                cls = mapper.class_
                await s.execute(delete(cls).where(cls.user_id == uid))
            for cls in (AuthSession, ConnectorToken, Credential):
                await s.execute(delete(cls).where(cls.user_id == uid))
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password"); sp.add_argument("email")
    ci = sub.add_parser("create-invite")
    ci.add_argument("--uses", type=int, default=1)
    ci.add_argument("--expires-days", type=int, default=14)
    sub.add_parser("list-users")
    du = sub.add_parser("delete-user"); du.add_argument("email")
    du.add_argument("--yes", action="store_true")
    a = p.parse_args()

    if a.cmd == "set-password":
        pw = getpass.getpass("New password: ")
        if pw != getpass.getpass("Again: "):
            raise SystemExit("passwords did not match")
        asyncio.run(set_password(a.email, pw))
        print("password set")
    elif a.cmd == "create-invite":
        print(asyncio.run(create_invite(a.uses, a.expires_days)))
    elif a.cmd == "list-users":
        for row in asyncio.run(list_users()):
            print(*row, sep="\t")
    elif a.cmd == "delete-user":
        if not a.yes:
            raise SystemExit("this deletes every run, plan and note the user has; add --yes")
        asyncio.run(delete_user(a.email))
        print("deleted")


if __name__ == "__main__":
    sys.exit(main())
