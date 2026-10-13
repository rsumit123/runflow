"""Each user connects their own Strava; the webhook finds the right user."""
import httpx
import pytest

import tenancy
from tests.multiuser_fixtures import client, session_cookie, setup_app

pytestmark = pytest.mark.no_user


def test_state_round_trips_and_rejects_tampering(monkeypatch):
    import config, strava_accounts
    monkeypatch.setattr(config, "SESSION_STATE_SECRET", "k")
    s = strava_accounts.sign_state(7, now=1000)
    assert strava_accounts.verify_state(s, now=1001) == 7
    uid, exp, nonce, sig = s.split(".")
    assert strava_accounts.verify_state(f"8.{exp}.{nonce}.{sig}", now=1001) is None
    assert strava_accounts.verify_state(s[:-1] + ("0" if s[-1] != "0" else "1"), now=1001) is None
    assert strava_accounts.verify_state(s, now=1000 + strava_accounts.STATE_TTL_SEC + 1) is None
    assert strava_accounts.verify_state("garbage", now=1001) is None


async def test_connect_needs_a_login_and_redirects_with_signed_state(monkeypatch):
    main, database = await setup_app(monkeypatch, "st1")
    import strava_accounts
    async with client(main) as c:
        assert (await c.get("/api/strava/connect")).status_code == 401
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.get("/api/strava/connect", follow_redirects=False)
    assert r.status_code == 302
    loc = httpx.URL(r.headers["location"])
    assert loc.host == "www.strava.com"
    assert strava_accounts.verify_state(loc.params["state"]) == 2


async def test_callback_stores_encrypted_tokens_for_the_state_user(monkeypatch):
    main, database = await setup_app(monkeypatch, "st2")
    import credentials, strava_accounts

    async def fake_exchange(code):
        assert code == "the-code"
        return {"access_token": "AT", "refresh_token": "RT", "expires_at": 123,
                "athlete": {"id": 555}}
    monkeypatch.setattr(strava_accounts, "exchange_code", fake_exchange)
    state = strava_accounts.sign_state(2)
    async with client(main) as c:  # no cookie: the user comes from the state
        r = await c.get("/api/strava/callback",
                        params={"code": "the-code", "state": state},
                        follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/settings?strava=connected")
    async with database.async_session() as s:
        cred = await credentials.get(s, 2, "strava")
    assert cred.data["refresh_token"] == "RT" and cred.external_id == "555"


async def test_callback_with_a_bad_state_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "st3")
    import credentials, strava_accounts

    async def fake_exchange(code):
        raise AssertionError("must not exchange a code for an unverified state")
    monkeypatch.setattr(strava_accounts, "exchange_code", fake_exchange)
    async with client(main) as c:
        r = await c.get("/api/strava/callback", params={"code": "x", "state": "1.2.3.4"},
                        follow_redirects=False)
    assert r.status_code == 302 and "strava=error" in r.headers["location"]
    async with database.async_session() as s:
        assert await credentials.get(s, 1, "strava") is None


async def test_athlete_cap_is_reported_as_capacity(monkeypatch):
    main, database = await setup_app(monkeypatch, "st4")
    import strava_accounts

    async def full(code):
        raise strava_accounts.StravaConnectError("capacity")
    monkeypatch.setattr(strava_accounts, "exchange_code", full)
    async with client(main) as c:
        r = await c.get("/api/strava/callback",
                        params={"code": "x", "state": strava_accounts.sign_state(2)},
                        follow_redirects=False)
    assert "reason=capacity" in r.headers["location"]


async def test_a_strava_athlete_links_to_only_one_user(monkeypatch):
    main, database = await setup_app(monkeypatch, "st9")
    import credentials, strava_accounts
    async with database.async_session() as s:
        await credentials.put(s, 1, "strava", {"access_token": "a", "refresh_token": "r"},
                              external_id="555")
        await s.commit()

    async def same_athlete(code):
        return {"access_token": "AT", "refresh_token": "RT", "athlete": {"id": 555}}
    monkeypatch.setattr(strava_accounts, "exchange_code", same_athlete)
    async with client(main) as c:
        r = await c.get("/api/strava/callback",
                        params={"code": "x", "state": strava_accounts.sign_state(2)},
                        follow_redirects=False)
    assert "reason=already_linked" in r.headers["location"]
    async with database.async_session() as s:
        assert await credentials.get(s, 2, "strava") is None


async def test_webhook_imports_for_the_owning_user_only(monkeypatch):
    main, database = await setup_app(monkeypatch, "st5")
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "a", "refresh_token": "r"},
                              external_id="555")
        await s.commit()
    seen = []

    async def capture(activity_id, user_id):
        seen.append((activity_id, user_id))
    monkeypatch.setattr(main, "_webhook_import_activity", capture)
    event = {"object_type": "activity", "aspect_type": "create", "object_id": 42}
    async with client(main) as c:
        await c.post("/api/webhook", json={**event, "owner_id": 555})
        await c.post("/api/webhook", json={**event, "owner_id": 999})
    assert seen == [(42, 2)]


async def test_deauthorize_event_removes_the_connection(monkeypatch):
    main, database = await setup_app(monkeypatch, "st6")
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "a", "refresh_token": "r"},
                              external_id="555")
        await s.commit()
    async with client(main) as c:
        await c.post("/api/webhook", json={"object_type": "athlete", "aspect_type": "update",
                                           "object_id": 555, "owner_id": 555,
                                           "updates": {"authorized": "false"}})
    async with database.async_session() as s:
        assert await credentials.get(s, 2, "strava") is None


async def test_refreshed_tokens_are_saved_to_the_users_credentials(monkeypatch):
    main, database = await setup_app(monkeypatch, "st7")
    import config, credentials, strava_accounts
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "old", "refresh_token": "r1"})
        await s.commit()

    def handler(request):
        assert str(request.url) == config.STRAVA_TOKEN_URL
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2",
                                         "expires_at": 999})
    c = await strava_accounts.client_for(2)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await c.refresh_access_token()
    async with database.async_session() as s:
        cred = await credentials.get(s, 2, "strava")
    assert cred.data["access_token"] == "new" and cred.data["refresh_token"] == "r2"


async def test_a_user_without_strava_gets_a_clear_409(monkeypatch):
    main, database = await setup_app(monkeypatch, "st8")
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/import/sync")
    assert r.status_code == 409 and "strava" in r.json()["detail"].lower()


def test_tokens_are_never_written_to_the_env_file():
    import config
    assert not hasattr(config, "update_env_tokens")
