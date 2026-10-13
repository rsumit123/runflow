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


async def test_unknown_email_still_costs_a_password_check(monkeypatch):
    """Without the dummy check, an unknown email answers measurably faster than
    a wrong password, and timing reveals who has an account."""
    database = await _fresh(monkeypatch, "a7")
    import auth
    async with database.async_session() as s:
        await auth.create_user(s, "real@b.co", "a long password")
        await s.commit()
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password",
                        lambda pw, h: calls.append(h) or real(pw, h))
    async with database.async_session() as s:
        assert await auth.authenticate(s, "ghost@b.co", "a long password") is None
        assert await auth.authenticate(s, "real@b.co", "wrong password!!") is None
    assert len(calls) == 2  # one bcrypt check per attempt, whether or not the email exists


async def test_malformed_email_is_just_a_failed_login(monkeypatch):
    database = await _fresh(monkeypatch, "a8")
    import auth
    async with database.async_session() as s:
        assert await auth.authenticate(s, "not-an-email", "a long password") is None


# --- fix round ---------------------------------------------------------------

async def test_same_email_signup_race_is_a_clean_error(monkeypatch):
    import asyncio
    database = await _fresh(monkeypatch, "r1")
    import auth
    from models import Invite, User
    async with database.async_session() as s:
        code = await auth.create_invite(s, created_by=None, uses=5)
        await s.commit()

    async def attempt():
        async with database.async_session() as s:
            try:
                u = await auth.signup(s, "race@x.co", "a long password", code)
                await s.commit()
                return u.id
            except Exception as e:  # noqa: BLE001 - classify below
                await s.rollback()
                return e

    results = await asyncio.gather(*[attempt() for _ in range(5)])
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(results) - len(errors) == 1
    assert len(errors) == 4
    assert all(isinstance(e, auth.AuthError) for e in errors), errors
    async with database.async_session() as s:
        assert len((await s.execute(select(User.id))).scalars().all()) == 1
        assert (await s.execute(select(Invite.uses_left))).scalar_one() == 4


async def test_junk_invite_does_not_reveal_a_registered_email(monkeypatch):
    database = await _fresh(monkeypatch, "r2")
    import auth
    async with database.async_session() as s:
        await auth.create_user(s, "exists@x.co", "a long password")
        await s.commit()
    async with database.async_session() as s:
        with pytest.raises(auth.AuthError, match="not valid"):
            await auth.signup(s, "exists@x.co", "a long password", "junk-code")


async def test_duplicate_with_valid_invite_restores_the_use_without_a_rollback(monkeypatch):
    database = await _fresh(monkeypatch, "r3")
    import auth
    from models import Invite
    async with database.async_session() as s:
        await auth.create_user(s, "exists@x.co", "a long password")
        code = await auth.create_invite(s, created_by=None, uses=2)
        await s.commit()
    async with database.async_session() as s:
        with pytest.raises(auth.AuthError, match="already registered"):
            await auth.signup(s, "exists@x.co", "a long password", code)
        # No s.rollback(): the savepoint alone must have restored the use.
        assert (await s.execute(select(Invite.uses_left))).scalar_one() == 2


async def test_authenticate_does_not_block_the_event_loop(monkeypatch):
    import asyncio, time
    database = await _fresh(monkeypatch, "b1")
    import auth
    async with database.async_session() as s:
        await auth.create_user(s, "real@b.co", "a long password")
        await s.commit()
    gaps, stop = [], False

    async def ticker():
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    async with database.async_session() as s:
        await auth.authenticate(s, "real@b.co", "wrong password!!")
        await auth.authenticate(s, "ghost@b.co", "a long password")
    stop = True
    await t
    assert max(gaps) < 0.1, f"event loop stalled for {max(gaps):.3f}s"


async def test_session_slide_failure_does_not_log_the_user_out(monkeypatch):
    from sqlalchemy.exc import OperationalError
    database = await _fresh(monkeypatch, "s1")
    import auth
    from models import AuthSession
    async with database.async_session() as s:
        u = await auth.create_user(s, "a@b.co", "a long password")
        raw = await auth.create_session(s, u.id)
        row = (await s.execute(select(AuthSession))).scalar_one()
        row.expires_at = datetime.utcnow() + timedelta(days=1)  # forces a slide
        await s.commit()
    async with database.async_session() as s:
        async def locked():
            raise OperationalError("x", None, Exception("database is locked"))
        monkeypatch.setattr(s, "commit", locked)
        assert await auth.resolve_session(s, raw) == u.id


def test_limiter_blocked_never_inserts_keys():
    import auth
    rl = auth.LoginRateLimiter()
    for i in range(1000):
        rl.blocked(f"user{i}@x.co", f"10.0.{i % 250}.{i // 250}")
    assert len(rl._email) == 0 and len(rl._ip) == 0


def test_limiter_truncates_keys():
    import auth
    rl = auth.LoginRateLimiter()
    rl.record_failure("a" * 300 + "@x.co", "9" * 300)
    assert all(len(k) <= 254 for k in list(rl._email) + list(rl._ip))
    assert len(rl._email) == 1 and len(rl._ip) == 1
    # a long email is still blocked consistently
    for _ in range(5):
        rl.record_failure("a" * 300 + "@x.co", "9" * 300)
    assert rl.blocked("a" * 300 + "@x.co", "9" * 300)


def test_limiter_drops_keys_whose_entries_are_all_stale():
    import auth
    now = [1000.0]
    rl = auth.LoginRateLimiter(clock=lambda: now[0])
    rl.record_failure("a@b.co", "1.1.1.1")
    assert "a@b.co" in rl._email and "1.1.1.1" in rl._ip
    now[0] += 15 * 60 + 1
    assert not rl.blocked("a@b.co", "1.1.1.1")
    assert "a@b.co" not in rl._email and "1.1.1.1" not in rl._ip


def test_limiter_is_hard_capped():
    import auth
    rl = auth.LoginRateLimiter(max_keys=5)
    for i in range(50):
        rl.record_failure(f"u{i}@x.co", f"10.0.0.{i}")
        assert len(rl._email) <= 5 and len(rl._ip) <= 5
    # expired entries are swept before anything live is discarded
    now = [0.0]
    rl = auth.LoginRateLimiter(max_keys=5, clock=lambda: now[0])
    for i in range(5):
        rl.record_failure(f"old{i}@x.co", "1.1.1.1")
    now[0] += 15 * 60 + 1
    rl.record_failure("new@x.co", "1.1.1.1")
    rl.record_failure("new2@x.co", "1.1.1.1")
    assert set(rl._email) == {"new@x.co", "new2@x.co"}


async def test_one_strava_athlete_links_to_one_user(monkeypatch):
    database = await _fresh(monkeypatch, "c3")
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 7, "strava", {"refresh_token": "SECRET-RT"}, external_id="123")
        await s.commit()
    async with database.async_session() as s:
        with pytest.raises(credentials.AlreadyLinked) as ei:
            await credentials.put(s, 8, "strava", {"refresh_token": "SECRET-OTHER"},
                                  external_id="123")
        assert "SECRET" not in str(ei.value)
        await s.rollback()
    async with database.async_session() as s:
        assert await credentials.get(s, 8, "strava") is None
        # the owner re-saving its own athlete (token refresh) is fine
        await credentials.put(s, 7, "strava", {"refresh_token": "NEW"}, external_id="123")
        await s.commit()
        # garmin rows carry no external id and may coexist
        await credentials.put(s, 7, "garmin", {"t": 1})
        await credentials.put(s, 8, "garmin", {"t": 2})
        await s.commit()


async def test_credential_external_id_is_unique_in_the_schema(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    database = await _fresh(monkeypatch, "c4")
    from models import Credential
    now = datetime.utcnow()
    async with database.async_session() as s:
        for uid in (1, 2):
            s.add(Credential(user_id=uid, provider="strava", secret_blob="x",
                             external_id="123", status="ok", updated_at=now))
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_unusable_hash_costs_exactly_one_dummy_check(monkeypatch):
    database = await _fresh(monkeypatch, "u1")
    import auth
    async with database.async_session() as s:
        await auth.create_user(s, "owner@b.co", None)
        await s.commit()
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password",
                        lambda pw, h: calls.append(h) or real(pw, h))
    async with database.async_session() as s:
        assert await auth.authenticate(s, "owner@b.co", "a long password") is None
    assert calls == [auth._DUMMY_HASH]


def test_normalize_email_hardening():
    import auth
    assert auth.normalize_email("ＡＢ@x.co") == "ab@x.co"
    for bad in ("a b@x.co", "a@b", "a@b@c.co", "a\tb@x.co", "a\x00b@x.co"):
        with pytest.raises(auth.AuthError):
            auth.normalize_email(bad)
