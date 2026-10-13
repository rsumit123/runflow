"""Fixes from the final whole-branch review."""
import asyncio
import time

import pytest
from cryptography.fernet import Fernet

import tenancy
from tests.multiuser_fixtures import client, session_cookie, setup_app

pytestmark = pytest.mark.no_user


async def test_user_ids_are_not_reused_after_a_delete(monkeypatch):
    await setup_app(monkeypatch, "rv1")
    import admin, auth, database
    async with database.async_session() as s:
        await auth.create_user(s, "c@example.com", "c long password")
        await s.commit()
    await admin.delete_user("c@example.com")
    async with database.async_session() as s:
        u = await auth.create_user(s, "d@example.com", "d long password")
        await s.commit()
        assert u.id == 4


def test_delete_user_says_to_restart_the_backend(monkeypatch, capsys):
    async def prepare():
        await setup_app(monkeypatch, "rv2")
        import auth, database
        async with database.async_session() as s:
            await auth.create_user(s, "c@example.com", "c long password")
            await s.commit()
    asyncio.run(prepare())
    import admin
    monkeypatch.setattr("sys.argv", ["admin.py", "delete-user", "c@example.com", "--yes"])
    admin.main()
    assert "Restart the backend container" in capsys.readouterr().out


async def test_a_cached_strava_client_is_not_returned_after_its_credential_changes(monkeypatch):
    await setup_app(monkeypatch, "rv3")
    import credentials, database, strava_accounts
    from strava_client import StravaNotConnected
    strava_accounts._clients.clear()

    async def put(rt):
        async with database.async_session() as s:
            await credentials.put(s, 2, "strava", {"access_token": "AT", "refresh_token": rt})
            await s.commit()
    await put("RT1")
    c1 = await strava_accounts.client_for(2)
    assert await strava_accounts.client_for(2) is c1
    await put("RT2")                       # replaced behind the cache's back
    c2 = await strava_accounts.client_for(2)
    assert c2 is not c1 and c2.refresh_token == "RT2"
    async with database.async_session() as s:
        await credentials.delete(s, 2, "strava")
        await s.commit()
    with pytest.raises(StravaNotConnected):
        await strava_accounts.client_for(2)
    assert 2 not in strava_accounts._clients


async def test_lifespan_refuses_to_start_in_production_without_a_valid_key(monkeypatch):
    main, database = await setup_app(monkeypatch, "rv4")
    import config

    async def idle():
        await asyncio.sleep(3600)
    monkeypatch.setattr(main, "_auto_sync_loop", idle)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "CREDENTIALS_KEY", "")
    with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
        async with main.lifespan(main.app):
            pass
    monkeypatch.setattr(config, "CREDENTIALS_KEY", "not-a-fernet-key")
    with pytest.raises(RuntimeError, match="CREDENTIALS_KEY"):
        async with main.lifespan(main.app):
            pass
    monkeypatch.setattr(config, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "SESSION_STATE_SECRET", "")
    with pytest.raises(RuntimeError, match="SESSION_STATE_SECRET"):
        async with main.lifespan(main.app):
            pass


async def test_lifespan_starts_without_keys_when_not_secure(monkeypatch):
    main, database = await setup_app(monkeypatch, "rv5")
    import config

    async def idle():
        await asyncio.sleep(3600)
    monkeypatch.setattr(main, "_auto_sync_loop", idle)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(config, "CREDENTIALS_KEY", "")
    monkeypatch.setattr(config, "SESSION_STATE_SECRET", "")
    async with main.lifespan(main.app):
        pass


async def test_signup_is_throttled_per_ip_before_any_hashing(monkeypatch):
    main, database = await setup_app(monkeypatch, "rv6")
    import auth, auth_routes
    auth_routes.signup_limiter = auth.LoginRateLimiter(per_email=10**9, per_ip=10)
    calls = []
    real = auth.hash_password
    monkeypatch.setattr(auth, "hash_password", lambda p: calls.append(1) or real(p))
    body = {"email": "x@example.com", "password": "a long password", "invite_code": "nope"}
    async with client(main) as c:
        codes = [(await c.post("/api/auth/signup", json=body,
                               headers={"X-Real-IP": "9.9.9.9"})).status_code
                 for _ in range(11)]
        other = await c.post("/api/auth/signup", json=body, headers={"X-Real-IP": "8.8.8.8"})
    assert codes[:10] == [400] * 10 and codes[10] == 429
    assert other.status_code == 400
    assert len(calls) == 11  # 10 from the first IP, 1 from the other; none for the 11th


class GatedStrava:
    def __init__(self, gate):
        self.gate, self.entered = gate, asyncio.Event()
        self.refresh_token = 'RT'

    async def get_activities(self, page=1, per_page=50, **kw):
        if page > 1:
            return []
        return [{"id": 7001 + i, "name": "r", "sport_type": "Run", "distance": 5000.0,
                 "moving_time": 1800, "elapsed_time": 1800,
                 "start_date": "2026-01-0%dT06:00:00Z" % (i + 1), "average_speed": 2.7}
                for i in range(3)]

    async def get_activity_detail(self, aid):
        return {"splits_metric": []}

    async def get_activity_streams(self, aid):
        # By now the activity row has been flushed: a write transaction is open.
        self.entered.set()
        await self.gate.wait()
        return []

    async def close(self):
        pass


async def _store_strava(database):
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "AT", "refresh_token": "RT"})
        await s.commit()


@pytest.mark.parametrize("route", ["/api/import/sync", "/api/import/by-date"])
async def test_an_import_does_not_block_another_users_login(monkeypatch, route):
    main, database = await setup_app(monkeypatch, "rv7" + route[-4:])
    import strava_accounts
    gate = asyncio.Event()
    fake = GatedStrava(gate)
    strava_accounts._clients.clear()
    strava_accounts._clients[2] = fake
    await _store_strava(database)
    cookie = await session_cookie(database, 2)
    payload = {"start_date": "2026-01-01", "end_date": "2026-02-01"}

    async def run_import():
        async with client(main, cookie) as c:
            return await c.post(route, json=payload) if "date" in route else await c.post(route)
    task = asyncio.create_task(run_import())
    try:
        await asyncio.wait_for(fake.entered.wait(), 10)
        t0 = time.monotonic()
        await asyncio.wait_for(session_cookie(database, 1), 10)  # create_session + commit
        assert time.monotonic() - t0 < 2
    finally:
        gate.set()
        r = await task
    assert r.status_code == 200, r.text


async def test_bg_import_does_not_block_another_users_login(monkeypatch):
    main, database = await setup_app(monkeypatch, "rv8")
    import strava_accounts
    gate = asyncio.Event()
    fake = GatedStrava(gate)
    strava_accounts._clients.clear()
    strava_accounts._clients[2] = fake
    await _store_strava(database)
    main.import_progress["j"] = {"user_id": 2}

    async def run():
        with tenancy.user_context(2):
            await main._bg_import_all("j")
    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(fake.entered.wait(), 10)
        t0 = time.monotonic()
        await asyncio.wait_for(session_cookie(database, 1), 10)
        assert time.monotonic() - t0 < 2
    finally:
        gate.set()
        await task
    assert main.import_progress["j"]["status"] == "completed"


async def test_client_for_during_a_refresh_write_returns_the_same_live_client(monkeypatch):
    """The client's in-memory token runs ahead of the database while on_refresh's
    write waits; that must not read as a replaced credential."""
    import httpx
    await setup_app(monkeypatch, "rv9")
    import credentials, database, strava_accounts
    strava_accounts._clients.clear()
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "AT", "refresh_token": "RT1"})
        await s.commit()
    c1 = await strava_accounts.client_for(2)

    def token_endpoint(request):
        return httpx.Response(200, json={"access_token": "AT2", "refresh_token": "RT2",
                                         "expires_at": 1})
    c1._client = httpx.AsyncClient(transport=httpx.MockTransport(token_endpoint))

    entered, gate = asyncio.Event(), asyncio.Event()
    real_put = credentials.put

    async def slow_put(*a, **k):
        entered.set()
        await gate.wait()           # the DB write is stuck behind another writer
        return await real_put(*a, **k)
    monkeypatch.setattr(credentials, "put", slow_put)

    refresh = asyncio.create_task(c1.refresh_access_token())
    await asyncio.wait_for(entered.wait(), 10)
    assert c1.refresh_token == "RT2"                       # memory is ahead of the DB
    c_again = await strava_accounts.client_for(2)
    assert c_again is c1 and not c1._client.is_closed
    gate.set()
    await refresh
    async with database.async_session() as s:
        cred = await credentials.get(s, 2, "strava")
    assert cred.data["refresh_token"] == "RT2" and cred.status == "ok"
    assert await strava_accounts.client_for(2) is c1


def _status_error(code):
    import httpx
    req = httpx.Request("GET", "https://strava.test/streams")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


class FailingStreams:
    refresh_token = "RT"
    error = RuntimeError("strava hiccup")

    async def get_activity_detail(self, aid):
        return {"splits_metric": []}

    async def get_activity_streams(self, aid):
        raise self.error

    async def close(self):
        pass


@pytest.mark.parametrize("detailed", [True, False])
@pytest.mark.parametrize("error", ["generic", "500", "conn"])
async def test_a_failed_streams_fetch_keeps_existing_streams(monkeypatch, detailed, error):
    import httpx
    err = {"generic": RuntimeError("strava hiccup"), "500": _status_error(500),
           "conn": httpx.ConnectError("down")}[error]
    n, act = await _reimport(monkeypatch, "rv10%s%s" % (detailed, error), err, detailed)
    assert n == 1
    assert bool(act.has_detailed_data) is detailed


async def test_a_404_for_streams_means_no_data_not_a_failed_fetch(monkeypatch):
    n, act = await _reimport(monkeypatch, "rv11", _status_error(404), False)
    assert n == 0 and act.has_detailed_data is True


async def _reimport(monkeypatch, tag, error, detailed):
    main, database = await setup_app(monkeypatch, tag)
    import strava_accounts
    from datetime import datetime
    from sqlalchemy import select, func
    from models import Activity, Stream
    strava_accounts._clients.clear()
    fake = FailingStreams()
    fake.error = error
    strava_accounts._clients[2] = fake
    await _store_strava(database)
    with tenancy.user_context(2):
        async with database.async_session() as s:
            s.add(Activity(id=5001, name="r", sport_type="Run", distance=5000.0,
                           start_date=datetime(2026, 1, 1), has_detailed_data=detailed))
            if detailed or error is not None and getattr(error, "response", None) is None \
                    or getattr(getattr(error, "response", None), "status_code", 0) != 404:
                s.add(Stream(activity_id=5001, stream_type="time", data=[0, 1, 2]))
            await s.commit()
        async with database.async_session() as s:
            act = await s.get(Activity, 5001)
            await main._import_detail_and_streams(s, act)
            await s.commit()
        async with database.async_session() as s:
            n = (await s.execute(select(func.count(Stream.id)).where(
                Stream.activity_id == 5001))).scalar()
            return n, await s.get(Activity, 5001)
