"""Passwords, sessions, invites, rate limiting and encrypted credentials."""
import tempfile
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

pytestmark = pytest.mark.no_user


async def _fresh(monkeypatch, tag):
    import importlib, sys, config, database
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=f"{tag}.db"))
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401
    await database.init_db()
    return database


def test_password_hash_round_trips_and_rejects_wrong_password():
    import auth
    h = auth.hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("correct horse batterz", h)


def test_unusable_hash_never_verifies():
    import auth
    assert not auth.verify_password("", auth.UNUSABLE_PASSWORD)
    assert not auth.verify_password("anything at all", auth.UNUSABLE_PASSWORD)


@pytest.mark.parametrize("pw", ["short", "x" * 73])
def test_password_policy(pw):
    import auth
    with pytest.raises(auth.AuthError):
        auth.check_password_policy(pw)


async def test_session_round_trip_and_expiry(monkeypatch):
    database = await _fresh(monkeypatch, "a1")
    import auth
    async with database.async_session() as s:
        u = await auth.create_user(s, "Runner@Example.com", "a long password")
        raw = await auth.create_session(s, u.id)
        await s.commit()
    assert u.email == "runner@example.com"
    async with database.async_session() as s:
        assert await auth.resolve_session(s, raw) == u.id
        assert await auth.resolve_session(s, raw + "x") is None
        assert await auth.resolve_session(s, None) is None
    # The DB holds a hash, never the cookie value.
    from models import AuthSession
    async with database.async_session() as s:
        stored = (await s.execute(select(AuthSession.token_hash))).scalar_one()
    assert stored != raw and len(stored) == 64
    # Expired sessions do not resolve.
    async with database.async_session() as s:
        row = (await s.execute(select(AuthSession))).scalar_one()
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await s.commit()
    async with database.async_session() as s:
        assert await auth.resolve_session(s, raw) is None


async def test_end_session_logs_out(monkeypatch):
    database = await _fresh(monkeypatch, "a2")
    import auth
    async with database.async_session() as s:
        u = await auth.create_user(s, "a@b.co", "a long password")
        raw = await auth.create_session(s, u.id)
        await s.commit()
    async with database.async_session() as s:
        await auth.end_session(s, raw)
        await s.commit()
    async with database.async_session() as s:
        assert await auth.resolve_session(s, raw) is None


async def test_authenticate(monkeypatch):
    database = await _fresh(monkeypatch, "a3")
    import auth
    async with database.async_session() as s:
        await auth.create_user(s, "a@b.co", "a long password")
        await s.commit()
    async with database.async_session() as s:
        assert (await auth.authenticate(s, "A@B.co", "a long password")) is not None
        assert await auth.authenticate(s, "a@b.co", "wrong password!") is None
        assert await auth.authenticate(s, "nobody@b.co", "a long password") is None


async def test_invites_are_single_use_and_stored_hashed(monkeypatch):
    database = await _fresh(monkeypatch, "a4")
    import auth
    from models import Invite
    async with database.async_session() as s:
        code = await auth.create_invite(s, created_by=None, uses=1)
        await s.commit()
    async with database.async_session() as s:
        assert (await s.execute(select(Invite.code_hash))).scalar_one() != code
    async with database.async_session() as s:
        u = await auth.signup(s, "first@x.co", "a long password", code)
        await s.commit()
    assert u.id
    async with database.async_session() as s:
        with pytest.raises(auth.AuthError):
            await auth.signup(s, "second@x.co", "a long password", code)


async def test_expired_invite_is_refused(monkeypatch):
    database = await _fresh(monkeypatch, "a5")
    import auth
    from models import Invite
    async with database.async_session() as s:
        code = await auth.create_invite(s, created_by=None, uses=3, expires_days=1)
        await s.commit()
    async with database.async_session() as s:
        row = (await s.execute(select(Invite))).scalar_one()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        await s.commit()
    async with database.async_session() as s:
        with pytest.raises(auth.AuthError):
            await auth.signup(s, "x@x.co", "a long password", code)


async def test_duplicate_email_does_not_consume_the_invite(monkeypatch):
    database = await _fresh(monkeypatch, "a6")
    import auth
    from models import Invite
    async with database.async_session() as s:
        await auth.create_user(s, "taken@x.co", "a long password")
        code = await auth.create_invite(s, created_by=None, uses=1)
        await s.commit()
    async with database.async_session() as s:
        with pytest.raises(auth.AuthError):
            await auth.signup(s, "TAKEN@x.co", "a long password", code)
        await s.rollback()
    async with database.async_session() as s:
        assert (await s.execute(select(Invite.uses_left))).scalar_one() == 1


def test_rate_limiter_blocks_after_five_failures_per_email():
    import auth
    now = [1000.0]
    rl = auth.LoginRateLimiter(clock=lambda: now[0])
    for _ in range(5):
        assert not rl.blocked("a@b.co", "1.1.1.1")
        rl.record_failure("a@b.co", "1.1.1.1")
    assert rl.blocked("a@b.co", "1.1.1.1")
    assert not rl.blocked("other@b.co", "2.2.2.2")
    now[0] += 15 * 60 + 1
    assert not rl.blocked("a@b.co", "1.1.1.1")


def test_rate_limiter_success_clears_the_email():
    import auth
    rl = auth.LoginRateLimiter()
    for _ in range(4):
        rl.record_failure("a@b.co", "1.1.1.1")
    rl.record_success("a@b.co")
    for _ in range(4):
        rl.record_failure("a@b.co", "1.1.1.1")
    assert not rl.blocked("a@b.co", "1.1.1.1")


async def test_credentials_are_encrypted_at_rest(monkeypatch):
    database = await _fresh(monkeypatch, "c1")
    import credentials
    from models import Credential
    async with database.async_session() as s:
        await credentials.put(s, 7, "strava", {"refresh_token": "SECRET-RT"},
                              external_id="123")
        await s.commit()
    async with database.async_session() as s:
        blob = (await s.execute(select(Credential.secret_blob))).scalar_one()
        assert "SECRET-RT" not in blob
        cred = await credentials.get(s, 7, "strava")
        assert cred.data == {"refresh_token": "SECRET-RT"}
        assert cred.external_id == "123" and cred.status == "ok"
        assert await credentials.user_for_external(s, "strava", "123") == 7
        assert await credentials.user_for_external(s, "strava", "999") is None
        assert await credentials.users_with(s, "strava") == [7]
        await credentials.set_status(s, 7, "strava", "needs_reconnect")
        assert await credentials.users_with(s, "strava") == []
        await credentials.delete(s, 7, "strava")
        assert await credentials.get(s, 7, "strava") is None


async def test_credentials_refuse_to_work_without_a_key(monkeypatch):
    database = await _fresh(monkeypatch, "c2")
    import config, credentials
    monkeypatch.setattr(config, "CREDENTIALS_KEY", "")
    async with database.async_session() as s:
        with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
            await credentials.put(s, 7, "strava", {"x": 1})
