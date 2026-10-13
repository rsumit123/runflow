"""Each user connects their own Strava; the webhook finds the right user."""
import httpx
import pytest

import tenancy
from tests.multiuser_fixtures import client, session_cookie, setup_app

pytestmark = pytest.mark.no_user


async def _callback(main, database, uid, *, nonce="n0nce", cookie_nonce="n0nce",
                    session_for="same", code="the-code", state=None):
    """GET the callback the way a browser does: the nonce cookie set by /connect,
    plus (usually) the login cookie. None leaves a cookie out."""
    import config, strava_accounts
    state = state or strava_accounts.sign_state(uid, nonce=nonce)
    parts = []
    if cookie_nonce is not None:
        parts.append(f"{config.STRAVA_OAUTH_COOKIE_NAME}={cookie_nonce}")
    who = uid if session_for == "same" else session_for
    if who is not None:
        parts.append(f"{config.SESSION_COOKIE_NAME}={await session_cookie(database, who)}")
    headers = {"Cookie": "; ".join(parts)} if parts else {}
    async with client(main) as c:
        return await c.get("/api/strava/callback", params={"code": code, "state": state},
                           headers=headers, follow_redirects=False)


def _fake_exchange(monkeypatch, athlete=555):
    import strava_accounts

    async def fake(code):
        return {"access_token": "AT", "refresh_token": "RT", "expires_at": 123,
                "athlete": {"id": athlete}}
    monkeypatch.setattr(strava_accounts, "exchange_code", fake)


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
    nonce = loc.params["state"].split(".")[2]
    setc = r.headers["set-cookie"]
    assert f"__Host-strava_oauth={nonce}" in setc
    low = setc.lower()
    assert "httponly" in low and "secure" in low and "samesite=lax" in low
    assert "max-age=600" in low and "path=/;" in low + ";" and "domain=" not in low


async def test_callback_stores_encrypted_tokens_for_the_state_user(monkeypatch):
    main, database = await setup_app(monkeypatch, "st2")
    import credentials, strava_accounts

    async def fake_exchange(code):
        assert code == "the-code"
        return {"access_token": "AT", "refresh_token": "RT", "expires_at": 123,
                "athlete": {"id": 555}}
    monkeypatch.setattr(strava_accounts, "exchange_code", fake_exchange)
    r = await _callback(main, database, 2)
    assert r.status_code == 302 and r.headers["location"].endswith("/settings?strava=connected")
    assert "__Host-strava_oauth=" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
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
    r = await _callback(main, database, 2, code="x")
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
    r = await _callback(main, database, 2, code="x")
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
    import credentials, strava_accounts

    async def revoked(user_id):
        return True
    monkeypatch.setattr(strava_accounts, "token_revoked", revoked)
    async with database.async_session() as s:
        await credentials.put(s, 2, "strava", {"access_token": "a", "refresh_token": "r"},
                              external_id="555")
        await s.commit()
    async with client(main) as c:
        await c.post("/api/webhook", json={"object_type": "athlete", "aspect_type": "update",
                                           "object_id": 555, "owner_id": 555,
                                           "updates": {"authorized": "false"}})
    await _drain(main)
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


# --- review round: OAuth bound to the browser and session -------------------

async def _stored(database, uid):
    import credentials
    async with database.async_session() as s:
        return await credentials.get(s, uid, "strava")


async def test_valid_state_without_the_nonce_cookie_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1a")
    _fake_exchange(monkeypatch)
    r = await _callback(main, database, 2, cookie_nonce=None)
    assert "reason=state" in r.headers["location"]
    assert await _stored(database, 2) is None


async def test_a_different_nonce_cookie_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1b")
    _fake_exchange(monkeypatch)
    r = await _callback(main, database, 2, cookie_nonce="someone-elses")
    assert "reason=state" in r.headers["location"]
    assert await _stored(database, 2) is None


async def test_valid_state_and_nonce_but_no_session_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1c")
    _fake_exchange(monkeypatch)
    r = await _callback(main, database, 2, session_for=None)
    assert "reason=signin" in r.headers["location"]
    assert await _stored(database, 2) is None


async def test_valid_state_and_nonce_with_another_users_session_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1d")
    _fake_exchange(monkeypatch)
    r = await _callback(main, database, 2, session_for=1)  # the victim is user 1
    assert "reason=state" in r.headers["location"]
    assert await _stored(database, 2) is None and await _stored(database, 1) is None


async def test_every_callback_outcome_clears_the_nonce_cookie(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1e")
    r = await _callback(main, database, 2, cookie_nonce=None)
    sc = r.headers["set-cookie"].lower()
    assert "__host-strava_oauth=" in sc and "max-age=0" in sc and "path=/" in sc and "domain=" not in sc


async def test_replaying_a_used_state_stores_nothing(monkeypatch):
    main, database = await setup_app(monkeypatch, "c1f")
    import credentials, strava_accounts
    _fake_exchange(monkeypatch)
    state = strava_accounts.sign_state(2, nonce="once")
    r1 = await _callback(main, database, 2, nonce="once", cookie_nonce="once", state=state)
    assert "strava=connected" in r1.headers["location"]
    async with database.async_session() as s:
        await credentials.delete(s, 2, "strava")
        await s.commit()
    # The browser dropped the cookie when the first callback answered.
    r2 = await _callback(main, database, 2, cookie_nonce=None, state=state)
    assert "reason=state" in r2.headers["location"]
    assert await _stored(database, 2) is None


async def test_a_non_ascii_state_is_rejected_not_a_500(monkeypatch):
    main, database = await setup_app(monkeypatch, "c4")
    async with client(main) as c:
        r = await c.get("/api/strava/callback",
                        params={"code": "x", "state": "1.99999999999.n.\u00e9"},
                        follow_redirects=False)
    assert r.status_code == 302 and "reason=state" in r.headers["location"]


# --- forged webhooks ---------------------------------------------------------

async def _link(database, uid=2, ext="555"):
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, uid, "strava", {"access_token": "a", "refresh_token": "r"},
                              external_id=ext)
        await s.commit()


async def _drain(main):
    """Wait for the detached deauthorize confirmations the webhook started."""
    import asyncio
    pending = list(main._detached)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


_DEAUTH = {"object_type": "athlete", "aspect_type": "update", "object_id": 555,
           "owner_id": 555, "updates": {"authorized": "false"}}


async def test_a_forged_deauthorize_keeps_the_credential(monkeypatch):
    main, database = await setup_app(monkeypatch, "w1")
    import strava_accounts
    await _link(database)

    async def still_valid(user_id):
        return False
    monkeypatch.setattr(strava_accounts, "token_revoked", still_valid)
    async with client(main) as c:
        r = await c.post("/api/webhook", json=_DEAUTH)
    assert r.status_code == 200
    await _drain(main)
    assert await _stored(database, 2) is not None


async def test_a_confirmed_deauthorize_forgets_the_credential(monkeypatch):
    main, database = await setup_app(monkeypatch, "w2")
    import strava_accounts
    await _link(database)

    async def revoked(user_id):
        return True
    monkeypatch.setattr(strava_accounts, "token_revoked", revoked)
    async with client(main) as c:
        await c.post("/api/webhook", json=_DEAUTH)
    await _drain(main)
    assert await _stored(database, 2) is None


async def test_token_revoked_asks_strava_and_only_a_failed_refresh_means_revoked(monkeypatch):
    main, database = await setup_app(monkeypatch, "w2b")
    import config, strava_accounts
    await _link(database)
    c = await strava_accounts.client_for(2)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"id": 555})))
    assert await strava_accounts.token_revoked(2) is False
    # Access token expired AND refresh rejected: that is a revoked grant.
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(400 if str(req.url) == config.STRAVA_TOKEN_URL else 401)))
    assert await strava_accounts.token_revoked(2) is True


async def test_a_wrong_subscription_id_is_ignored_when_configured(monkeypatch):
    main, database = await setup_app(monkeypatch, "w3")
    import config
    await _link(database)
    monkeypatch.setattr(config, "STRAVA_SUBSCRIPTION_ID", 77)
    seen = []

    async def capture(activity_id, user_id):
        seen.append((activity_id, user_id))
    monkeypatch.setattr(main, "_webhook_import_activity", capture)
    ev = {"object_type": "activity", "aspect_type": "create", "object_id": 42, "owner_id": 555}
    async with client(main) as c:
        assert (await c.post("/api/webhook", json={**ev, "subscription_id": 78})).status_code == 200
        assert (await c.post("/api/webhook", json=ev)).status_code == 200
        await c.post("/api/webhook", json={**ev, "subscription_id": 77})
    assert seen == [(42, 2)]


async def test_a_wrong_subscription_id_cannot_deauthorize(monkeypatch):
    main, database = await setup_app(monkeypatch, "w3b")
    import config, strava_accounts
    await _link(database)
    monkeypatch.setattr(config, "STRAVA_SUBSCRIPTION_ID", 77)

    async def revoked(user_id):
        return True
    monkeypatch.setattr(strava_accounts, "token_revoked", revoked)
    async with client(main) as c:
        await c.post("/api/webhook", json={**_DEAUTH, "subscription_id": 1})
    await _drain(main)
    assert await _stored(database, 2) is not None


async def _events(main, database, monkeypatch, bodies):
    await _link(database)
    seen = []

    async def capture(activity_id, user_id):
        seen.append((activity_id, user_id))
    monkeypatch.setattr(main, "_webhook_import_activity", capture)
    async with client(main) as c:
        for b in bodies:
            r = await c.post("/api/webhook", json={"object_type": "activity",
                                                   "aspect_type": "create",
                                                   "owner_id": 555, **b})
            assert r.status_code == 200
    return seen


async def test_a_non_integer_object_id_is_ignored(monkeypatch):
    main, database = await setup_app(monkeypatch, "w4")
    seen = await _events(main, database, monkeypatch,
                         [{"object_id": v} for v in ("../athlete", "42", 4.5, True, 0, -3, None, [1])])
    assert seen == []


async def test_a_duplicate_event_is_queued_once(monkeypatch):
    main, database = await setup_app(monkeypatch, "w5")
    seen = await _events(main, database, monkeypatch, [{"object_id": 42}, {"object_id": 42}])
    assert seen == [(42, 2)]


async def test_the_eleventh_concurrent_event_for_a_user_is_ignored(monkeypatch):
    main, database = await setup_app(monkeypatch, "w6")
    seen = await _events(main, database, monkeypatch, [{"object_id": i} for i in range(1, 13)])
    assert [a for a, _ in seen] == list(range(1, 11))


async def test_the_queue_slot_is_released_when_the_import_finishes(monkeypatch):
    main, database = await setup_app(monkeypatch, "w7")
    import strava_accounts

    async def no_sleep(_):
        return None
    monkeypatch.setattr(main._asyncio, "sleep", no_sleep)

    async def boom(activity_id):
        raise RuntimeError("strava down")

    class Proxy:
        get_activity_detail = staticmethod(boom)
    monkeypatch.setattr(main, "strava", Proxy())
    main._webhook_pending.setdefault(2, set()).add(42)
    await main._webhook_import_activity(42, 2)
    assert 42 not in main._webhook_pending.get(2, set())


# --- revoked refresh token, races ---------------------------------------------

async def test_a_rejected_refresh_gives_409_and_needs_reconnect_and_evicts(monkeypatch):
    main, database = await setup_app(monkeypatch, "r1")
    import config, strava_accounts
    await _link(database)
    cookie = await session_cookie(database, 2)
    c = await strava_accounts.client_for(2)
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(400 if str(req.url) == config.STRAVA_TOKEN_URL else 401)))
    async with client(main, cookie) as hc:
        r = await hc.post("/api/import/sync")
    assert r.status_code == 409 and "strava" in r.json()["detail"].lower()
    assert (await _stored(database, 2)).status == "needs_reconnect"
    assert 2 not in strava_accounts._clients


async def test_two_concurrent_refreshes_make_one_token_request():
    import asyncio
    import config
    from strava_client import StravaClient
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2",
                                         "expires_at": 9})
    c = StravaClient(access_token="old", refresh_token="r1")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await asyncio.gather(c.refresh_access_token(), c.refresh_access_token())
    assert calls == [config.STRAVA_TOKEN_URL] and c.access_token == "new"


async def test_a_refresh_after_disconnect_does_not_recreate_the_credential(monkeypatch):
    main, database = await setup_app(monkeypatch, "r2")
    import strava_accounts
    await _link(database)
    c = await strava_accounts.client_for(2)
    await strava_accounts.forget(2)
    await c._on_refresh({"access_token": "n", "refresh_token": "n", "expires_at": 1})
    assert await _stored(database, 2) is None


async def test_two_cold_requests_build_one_client(monkeypatch):
    import asyncio
    main, database = await setup_app(monkeypatch, "r3")
    import strava_accounts
    await _link(database)
    a, b = await asyncio.gather(strava_accounts.client_for(2), strava_accounts.client_for(2))
    assert a is b


# --- exchange failures ----------------------------------------------------------

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _mock_httpx(monkeypatch, handler):
    real = _REAL_ASYNC_CLIENT
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


async def test_exchange_maps_failures_to_reasons(monkeypatch):
    main, database = await setup_app(monkeypatch, "x1")
    import strava_accounts

    async def reason(handler):
        _mock_httpx(monkeypatch, handler)
        with pytest.raises(strava_accounts.StravaConnectError) as e:
            await strava_accounts.exchange_code("c")
        return e.value.reason
    assert await reason(lambda r: httpx.Response(429, text="Rate Limit Exceeded")) == "exchange_failed"
    assert await reason(lambda r: httpx.Response(403, text="athlete limit reached")) == "capacity"

    def down(request):
        raise httpx.ConnectError("no route")
    assert await reason(down) == "exchange_failed"
    assert await reason(lambda r: httpx.Response(200, text="<html>")) == "exchange_failed"
    assert await reason(lambda r: httpx.Response(200, json={"athlete": {}})) == "exchange_failed"


# --- final round: webhook rate limits, __Host- cookies, sign-in expiry --------

async def test_the_webhook_answers_before_a_slow_confirmation_finishes(monkeypatch):
    import asyncio, time
    main, database = await setup_app(monkeypatch, "f1")
    import strava_accounts
    await _link(database)

    async def slow(user_id):
        await asyncio.sleep(5)
        return True
    monkeypatch.setattr(strava_accounts, "token_revoked", slow)
    t0 = time.monotonic()
    async with client(main) as c:
        r = await c.post("/api/webhook", json=_DEAUTH)
    assert r.status_code == 200 and time.monotonic() - t0 < 1
    for t in list(main._detached):
        t.cancel()


async def test_a_second_deauthorize_inside_ten_minutes_makes_no_second_call(monkeypatch):
    main, database = await setup_app(monkeypatch, "f2")
    import strava_accounts
    await _link(database)
    now = [1000.0]
    monkeypatch.setattr(main, "_clock", lambda: now[0])
    calls = []

    async def keep(user_id):
        calls.append(user_id)
        return False
    monkeypatch.setattr(strava_accounts, "token_revoked", keep)
    async with client(main) as c:
        for _ in range(5):
            await c.post("/api/webhook", json=_DEAUTH)
        await _drain(main)
        assert calls == [2]
        now[0] += 601  # the window has passed
        await c.post("/api/webhook", json=_DEAUTH)
        await _drain(main)
    assert calls == [2, 2]


async def test_the_31st_activity_event_in_an_hour_is_ignored(monkeypatch):
    main, database = await setup_app(monkeypatch, "f3")
    await _link(database)
    now = [5000.0]
    monkeypatch.setattr(main, "_clock", lambda: now[0])
    seen = []

    async def capture(activity_id, user_id):
        seen.append(activity_id)
        main._webhook_pending[user_id].discard(activity_id)  # finished at once
    monkeypatch.setattr(main, "_webhook_import_activity", capture)

    async def post(i):
        async with client(main) as c:
            await c.post("/api/webhook", json={"object_type": "activity", "aspect_type": "create",
                                               "owner_id": 555, "object_id": i})
    for i in range(1, 32):
        now[0] += 1
        await post(i)
    assert seen == list(range(1, 31))
    now[0] += 3600  # the first ones age out of the rolling hour
    await post(99)
    assert seen[-1] == 99


async def test_cookies_use_the_host_prefix_when_secure(monkeypatch):
    main, database = await setup_app(monkeypatch, "f4")
    import config
    assert config.SESSION_COOKIE_NAME == "__Host-rf_session"
    assert config.STRAVA_OAUTH_COOKIE_NAME == "__Host-strava_oauth"
    async with client(main) as c:
        r = await c.post("/api/auth/login", json={"email": "a@example.com",
                                                  "password": "a long password"})
    assert r.status_code == 200
    low = r.headers["set-cookie"].lower()
    assert low.startswith("__host-rf_session=") and "path=/" in low and "domain=" not in low
    assert "secure" in low


def test_cookies_keep_plain_names_for_local_http(monkeypatch):
    import importlib, config
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")
    try:
        importlib.reload(config)
        assert config.SESSION_COOKIE_NAME == "rf_session"
        assert config.STRAVA_OAUTH_COOKIE_NAME == "strava_oauth"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


async def test_the_whole_flow_works_with_the_prefixed_cookies(monkeypatch):
    """connect sets the nonce cookie; the browser sends it back with the session."""
    main, database = await setup_app(monkeypatch, "f5")
    import config, strava_accounts
    _fake_exchange(monkeypatch)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.get("/api/strava/connect", follow_redirects=False)
        state = httpx.URL(r.headers["location"]).params["state"]
        nonce = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
        cb = await c.get("/api/strava/callback", params={"code": "x", "state": state},
                         headers={"Cookie": f"{config.STRAVA_OAUTH_COOKIE_NAME}={nonce}; "
                                            f"{config.SESSION_COOKIE_NAME}={cookie}"},
                         follow_redirects=False)
    assert "strava=connected" in cb.headers["location"]
    assert (await _stored(database, 2)) is not None


async def test_an_expired_session_says_signin_but_a_wrong_user_says_state(monkeypatch):
    from datetime import datetime, timedelta
    main, database = await setup_app(monkeypatch, "f6")
    import auth
    from models import AuthSession
    _fake_exchange(monkeypatch)
    raw = await session_cookie(database, 2)
    async with database.async_session() as s:
        row = await s.get(AuthSession, auth.hash_token(raw))
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        await s.commit()
    import config, strava_accounts
    state = strava_accounts.sign_state(2, nonce="n0nce")
    async with client(main) as c:
        r = await c.get("/api/strava/callback", params={"code": "x", "state": state},
                        headers={"Cookie": f"{config.STRAVA_OAUTH_COOKIE_NAME}=n0nce; "
                                           f"{config.SESSION_COOKIE_NAME}={raw}"},
                        follow_redirects=False)
    assert "reason=signin" in r.headers["location"]
    other = await _callback(main, database, 2, session_for=1)
    assert "reason=state" in other.headers["location"]
    mismatch = await _callback(main, database, 2, cookie_nonce="nope")
    assert "reason=state" in mismatch.headers["location"]
    assert await _stored(database, 2) is None


async def test_a_stale_clients_refresh_cannot_overwrite_a_reconnect(monkeypatch):
    import credentials
    main, database = await setup_app(monkeypatch, "f7")
    import strava_accounts
    await _link(database)
    old = await strava_accounts.client_for(2)  # holds refresh token "r"
    async with database.async_session() as s:   # the user reconnects meanwhile
        await credentials.put(s, 2, "strava", {"access_token": "NEW-A", "refresh_token": "NEW-R"},
                              external_id="555")
        await s.commit()
    await old._on_refresh({"access_token": "stale", "refresh_token": "stale-r", "expires_at": 1})
    cred = await _stored(database, 2)
    assert cred.data["refresh_token"] == "NEW-R" and cred.data["access_token"] == "NEW-A"
