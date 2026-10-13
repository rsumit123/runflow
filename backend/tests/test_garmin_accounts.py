"""Each user connects their own Garmin. The password is used once and never kept."""
import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import garminconnect
import garminconnect.client
import pytest
import requests
from garminconnect import GarminConnectAuthenticationError, GarminConnectConnectionError

import tenancy
from tests import multiuser_fixtures
from tests.multiuser_fixtures import client, session_cookie

pytestmark = pytest.mark.no_user

# Captured at import, before the autouse network guard replaces it.
REAL_GARMIN_LOGIN = garminconnect.Garmin.login
REAL_REFRESH_SESSION = garminconnect.client.Client._refresh_session


def rejected(msg="401"):
    """What the real library raises when Garmin refuses stored tokens: an auth error
    chained from the 'API Error 401' connection error of Client._run_request."""
    try:
        try:
            raise GarminConnectConnectionError("API Error 401 - Unauthorized")
        except GarminConnectConnectionError as e:
            raise GarminConnectAuthenticationError("Failed to retrieve social profile") from e
    except GarminConnectAuthenticationError as e:
        return e

PASSWORD = "hunter2-unique-PW-8841"


class Req:
    def __init__(self, body):
        self.body = body


class Resp:
    """Shaped like requests/curl_cffi responses: .request.body is what was sent."""
    text = "<csrf>"

    def __init__(self, body):
        self.request, self.history = Req(body), []

    def __repr__(self):
        return f"<Resp request.body={self.request.body!r}>"


class FakeClient:
    def __init__(self, tokens="T" * 600):
        self.tokens = tokens

    refresh_error = GarminConnectAuthenticationError(
        "DI token refresh failed: 401 invalid_grant")   # what Client._refresh_di_token raises
    refreshed = 0

    def dumps(self):
        return self.tokens

    def _refresh_di_token(self):
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refreshed += 1
        self.tokens = "F" * 600


class FakeGarmin:
    instances = []
    mfa = False
    reject = False
    restore_error = None
    in_login = hold = None
    widget = False

    def __init__(self, email=None, password=None, return_on_mfa=False, **kw):
        # Real attribute names: username / password.
        self.username, self.password = email, password
        self.mfa_pending = False
        self.client = FakeClient("E" * 600 if email else "T" * 600)
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        if tokenstore is not None:
            if FakeGarmin.restore_error is not None:
                raise FakeGarmin.restore_error
            self.client = FakeClient(tokenstore)
            return None, None
        if FakeGarmin.in_login is not None:
            FakeGarmin.in_login()
        if FakeGarmin.widget:
            # Real widget strategy keeps the last response, request body included.
            self.client._widget_last_resp = Resp(f"password={self.password}&embed=true")
        if FakeGarmin.reject:
            raise RuntimeError("401 Unauthorized")
        if FakeGarmin.mfa:
            # Real garminconnect 0.3.6: ("needs_mfa", None); the MFA state is
            # kept on the Garmin object itself, not returned.
            self.mfa_pending = True
            return "needs_mfa", None
        return None, None

    def resume_login(self, client_state, mfa_code):
        # Real Client.resume_login ignores client_state and uses its own state.
        assert client_state is None and self.mfa_pending
        if mfa_code != "123456":
            raise RuntimeError("bad code")
        return None, None


@pytest.fixture(autouse=True)
def fake_garmin():
    FakeGarmin.instances, FakeGarmin.mfa, FakeGarmin.reject = [], False, False
    FakeGarmin.restore_error = None
    FakeGarmin.in_login = FakeGarmin.hold = None
    FakeGarmin.widget = False
    yield


async def setup_app(monkeypatch, tag):
    """setup_app re-imports garmin_auth, so the fake must go into the NEW module.

    Without this the tests would drive the real garminconnect library.
    """
    main, database = await multiuser_fixtures.setup_app(monkeypatch, tag)
    import garmin_auth
    monkeypatch.setattr(garmin_auth, "Garmin", FakeGarmin)
    garmin_auth.reset()
    return main, database


async def test_connect_stores_tokens_and_never_the_password(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    main, database = await setup_app(monkeypatch, "g1")
    import config, credentials
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/garmin/connect",
                         json={"email": "b@garmin.co", "password": PASSWORD})
    assert r.status_code == 200 and r.json() == {"status": "connected"}
    async with database.async_session() as s:
        cred = await credentials.get(s, 2, "garmin")
    assert cred.data == {"tokens": "E" * 600}
    assert PASSWORD not in repr(cred.data)
    db = Path(str(__import__("database").DB_PATH))
    for f in (db, Path(f"{db}-wal")):
        if f.exists():
            assert PASSWORD.encode() not in f.read_bytes(), f
    assert PASSWORD not in caplog.text
    import garmin_auth
    assert all(PASSWORD != getattr(g, "password", None)
               for g in garmin_auth._clients.values())


async def test_mfa_flow(monkeypatch):
    main, database = await setup_app(monkeypatch, "g2")
    FakeGarmin.mfa = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r1 = await c.post("/api/garmin/connect",
                          json={"email": "b@garmin.co", "password": PASSWORD})
        bad = await c.post("/api/garmin/mfa", json={"code": "000000"})
        r2 = await c.post("/api/garmin/connect",
                          json={"email": "b@garmin.co", "password": PASSWORD})
        good = await c.post("/api/garmin/mfa", json={"code": "123456"})
        me = await c.get("/api/auth/me")
    assert r1.json() == {"status": "needs_mfa"}
    assert bad.status_code == 400
    assert r2.json() == {"status": "needs_mfa"}
    assert good.json() == {"status": "connected"}
    assert me.json()["garmin"] == "ok"


async def test_an_expired_mfa_attempt_must_start_again(monkeypatch):
    main, database = await setup_app(monkeypatch, "g3")
    import garmin_auth
    FakeGarmin.mfa = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
        g, state, started = garmin_auth._pending_mfa[2]
        garmin_auth._pending_mfa[2] = (g, state, started - garmin_auth.MFA_TTL_SEC - 1)
        r = await c.post("/api/garmin/mfa", json={"code": "123456"})
    assert r.status_code == 400 and "again" in r.json()["detail"].lower()
    assert 2 not in garmin_auth._pending_mfa


async def test_wrong_garmin_password_is_a_clear_400(monkeypatch):
    main, database = await setup_app(monkeypatch, "g4")
    FakeGarmin.reject = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
    assert r.status_code == 400 and PASSWORD not in r.text


async def test_each_user_gets_their_own_garmin(monkeypatch):
    main, database = await setup_app(monkeypatch, "g5")
    import credentials, garmin_auth
    async with database.async_session() as s:
        await credentials.put(s, 1, "garmin", {"tokens": "A" * 600})
        await credentials.put(s, 2, "garmin", {"tokens": "B" * 600})
        await s.commit()
    with tenancy.user_context(1):
        g1 = await garmin_auth.get_garmin()
    with tenancy.user_context(2):
        g2 = await garmin_auth.get_garmin()
    assert g1.client.tokens == "A" * 600 and g2.client.tokens == "B" * 600


async def test_no_garmin_means_garmin_not_connected(monkeypatch):
    main, database = await setup_app(monkeypatch, "g6")
    import garmin_auth
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.get_garmin()
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/import/garmin/sync")
    assert r.status_code == 409 and "garmin" in r.json()["detail"].lower()


async def test_rotated_tokens_are_saved(monkeypatch):
    main, database = await setup_app(monkeypatch, "g7")
    import credentials, garmin_auth
    async with database.async_session() as s:
        await credentials.put(s, 2, "garmin", {"tokens": "B" * 600})
        await s.commit()
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        g.client.tokens = "R" * 600       # the library refreshed its session
        await garmin_auth.save_if_rotated(2)
    async with database.async_session() as s:
        assert (await credentials.get(s, 2, "garmin")).data == {"tokens": "R" * 600}


async def test_disconnect_forgets_the_tokens(monkeypatch):
    main, database = await setup_app(monkeypatch, "g8")
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 2, "garmin", {"tokens": "B" * 600})
        await s.commit()
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        assert (await c.post("/api/garmin/disconnect")).status_code == 200
    async with database.async_session() as s:
        assert await credentials.get(s, 2, "garmin") is None


async def _seed(database, uid, tokens):
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, uid, "garmin", {"tokens": tokens})
        await s.commit()


async def _status(database, uid):
    import credentials
    async with database.async_session() as s:
        return await credentials.get(s, uid, "garmin")


async def test_user_without_garmin_cannot_reach_the_owners_garmin(monkeypatch):
    main, database = await setup_app(monkeypatch, "g9")
    await _seed(database, 1, "A" * 600)           # the owner is connected
    cookie = await session_cookie(database, 2)    # B is not
    async with client(main, cookie) as c:
        r = await c.delete("/api/workouts/adhoc/123")
    assert r.status_code == 409
    assert not any(getattr(g.client, "tokens", None) == "A" * 600
                   for g in FakeGarmin.instances)
    assert FakeGarmin.instances == []


async def test_rejected_tokens_mark_needs_reconnect(monkeypatch):
    main, database = await setup_app(monkeypatch, "g10")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    FakeGarmin.restore_error = rejected()
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "needs_reconnect"
    # And a later request is a 409 until they reconnect.
    FakeGarmin.restore_error = None
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.get_garmin()


async def test_a_transient_failure_does_not_disconnect(monkeypatch):
    main, database = await setup_app(monkeypatch, "g11")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    FakeGarmin.restore_error = ConnectionError("network down")
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminUnavailable):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "ok"


async def test_a_midcall_rejection_marks_needs_reconnect_and_is_a_409(monkeypatch):
    main, database = await setup_app(monkeypatch, "g12")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        def boom(*a):
            raise rejected()
        g.get_activities = boom
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.run(g, "get_activities", 0, 1)
    assert (await _status(database, 2)).status == "needs_reconnect"
    assert 2 not in garmin_auth._clients


async def test_a_reconnect_that_landed_meanwhile_is_not_clobbered(monkeypatch):
    main, database = await setup_app(monkeypatch, "g13")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        await _seed(database, 2, "N" * 600)       # user reconnects in between
        g.client.tokens = "R" * 600               # old client rotates
        await garmin_auth.save_if_rotated(2)      # must not overwrite the new grant
        def boom(*a):
            raise rejected()
        g.get_activities = boom
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.run(g, "get_activities", 0, 1)   # nor flag it
    cred = await _status(database, 2)
    assert cred.data == {"tokens": "N" * 600} and cred.status == "ok"


async def test_concurrent_cold_requests_build_one_client(monkeypatch):
    import asyncio
    main, database = await setup_app(monkeypatch, "g14")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        got = await asyncio.gather(*[garmin_auth.get_garmin() for _ in range(5)])
    assert all(x is got[0] for x in got)
    assert len(FakeGarmin.instances) == 1


async def test_the_password_is_dropped_from_the_pending_mfa_client(monkeypatch):
    main, database = await setup_app(monkeypatch, "g15")
    import garmin_auth
    FakeGarmin.mfa = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
    g = garmin_auth._pending_mfa[2][0]
    assert g.password is None


# ---- I1: an outage is not a rejection (REAL garminconnect classes) ----------

def _real_tokens():
    return json.dumps({"di_token": "a.b.c", "di_refresh_token": "r" * 600,
                       "di_client_id": "cid"})


async def _real_app(monkeypatch, tag):
    main, database = await multiuser_fixtures.setup_app(monkeypatch, tag)
    import garmin_auth
    garmin_auth.reset()
    monkeypatch.setattr(garminconnect.Garmin, "login", REAL_GARMIN_LOGIN)
    monkeypatch.setattr(garminconnect.time, "sleep", lambda s: None)
    # Library-internal refresh would hit the network; not what is under test.
    monkeypatch.setattr(garminconnect.client.Client, "_refresh_session", lambda self: None)
    return main, database, garmin_auth


class HttpResp:
    """What Client._http_post returns: .ok / .status_code / .text / .json()."""
    def __init__(self, code, body=None):
        self.status_code, self.ok, self.text, self._b = code, code < 400, "", body or {}

    def json(self):
        return self._b


def _refresh_endpoint(monkeypatch, answer):
    """Stub the DI refresh POST. answer(n) -> HttpResp (n counts calls from 1) or raises."""
    calls = {"n": 0}

    def post(self, url, **kw):
        calls["n"] += 1
        return answer(calls["n"])
    monkeypatch.setattr(garminconnect.client.Client, "_http_post", post)
    return calls


def _api(monkeypatch, good_when):
    """The API session: 401 unless good_when(bearer token) says the token works."""
    class Sess:
        def request(self, method, url, headers=None, **k):
            token = (headers or {}).get("Authorization", "").replace("Bearer ", "")
            if good_when(token):
                r = HttpResp(200, {"displayName": "u", "fullName": "f",
                                   "userData": {"measurementSystem": "metric"}})
                r.status_code = 200
                return r
            r = HttpResp(401)
            r.text = "Unauthorized"
            return r
    C = garminconnect.client.Client
    orig = C.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self._api_session = Sess()
    monkeypatch.setattr(C, "__init__", init)


async def test_expired_token_and_a_refresh_outage_is_not_a_revocation(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g40")
    monkeypatch.setattr(garminconnect.client.Client, "_refresh_session", REAL_REFRESH_SESSION)
    await _seed(database, 2, _real_tokens())
    _api(monkeypatch, lambda t: False)                       # access token expired
    _refresh_endpoint(monkeypatch, lambda n: HttpResp(503))  # ...and the refresh endpoint is down
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminUnavailable):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "ok"


async def test_a_refresh_endpoint_429_or_a_connection_error_is_also_an_outage(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g41")
    monkeypatch.setattr(garminconnect.client.Client, "_refresh_session", REAL_REFRESH_SESSION)
    await _seed(database, 2, _real_tokens())
    _api(monkeypatch, lambda t: False)
    for answer in (lambda n: HttpResp(429), lambda n: (_ for _ in ()).throw(requests.ConnectionError("x"))):
        _refresh_endpoint(monkeypatch, answer)
        with tenancy.user_context(2):
            with pytest.raises(garmin_auth.GarminUnavailable):
                await garmin_auth.get_garmin()
        assert (await _status(database, 2)).status == "ok"


async def test_expired_token_and_a_refused_refresh_needs_a_reconnect(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g42")
    monkeypatch.setattr(garminconnect.client.Client, "_refresh_session", REAL_REFRESH_SESSION)
    await _seed(database, 2, _real_tokens())
    _api(monkeypatch, lambda t: False)
    _refresh_endpoint(monkeypatch, lambda n: HttpResp(401))
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "needs_reconnect"


async def test_expired_token_and_a_working_refresh_connects_and_saves_the_new_tokens(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g43")
    monkeypatch.setattr(garminconnect.client.Client, "_refresh_session", REAL_REFRESH_SESSION)
    await _seed(database, 2, _real_tokens())
    new = "n" * 1500
    _api(monkeypatch, lambda t: t == new)
    # The library's own refresh (inside its API call, errors swallowed) is down for the
    # three profile attempts; ours, made afterwards to find out why, succeeds.
    _refresh_endpoint(monkeypatch, lambda n: HttpResp(503) if n <= 3 else HttpResp(
        200, {"access_token": new, "refresh_token": "rotated-" + "r" * 600}))
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
    assert g.client.di_token == new
    cred = await _status(database, 2)
    assert cred.status == "ok" and "rotated-" in json.loads(cred.data["tokens"])["di_refresh_token"]


async def test_run_classifies_a_401_by_what_the_refresh_endpoint_says(monkeypatch):
    main, database = await setup_app(monkeypatch, "g44")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        def api(*a):
            raise rejected()
        g.get_activities = api
        g.client.refresh_error = GarminConnectAuthenticationError("DI token refresh failed: 503 x")
        with pytest.raises(garmin_auth.GarminUnavailable):
            await garmin_auth.run(g, "get_activities", 0, 1)
        assert (await _status(database, 2)).status == "ok"
        # refresh works, the retried call works: the caller never notices
        calls = []
        def flaky(*a):
            calls.append(1)
            if len(calls) == 1:
                raise rejected()
            return ["activity"]
        g.get_activities = flaky
        g.client.refresh_error = None
        assert await garmin_auth.run(g, "get_activities", 0, 1) == ["activity"]
        assert len(calls) == 2 and g.client.refreshed == 1
    assert (await _status(database, 2)).data == {"tokens": "F" * 600}   # rotated and saved


async def test_a_garmin_outage_does_not_disconnect_the_user(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g20")
    assert len(_real_tokens()) > 512
    await _seed(database, 2, _real_tokens())

    def down(self, *a, **k):
        raise requests.ConnectionError("Garmin unreachable")
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", down)
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminUnavailable):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "ok"
    # ...and over HTTP it is a 503, not a 409 asking for a reconnect.
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.delete("/api/workouts/adhoc/5")
    assert r.status_code == 503 and "try again" in r.json()["detail"].lower()
    assert (await _status(database, 2)).status == "ok"


async def test_a_genuine_401_from_garmin_does_need_a_reconnect(monkeypatch):
    main, database, garmin_auth = await _real_app(monkeypatch, "g21")
    await _seed(database, 2, _real_tokens())

    def unauthorized(self, request, **k):
        r = requests.Response()
        r.status_code = 401
        r._content = b'{"message": "Unauthorized"}'
        return r
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", unauthorized)
    _refresh_endpoint(monkeypatch, lambda n: HttpResp(401))   # revoked: refresh refused too
    with tenancy.user_context(2):
        with pytest.raises(garmin_auth.GarminNotConnected):
            await garmin_auth.get_garmin()
    assert (await _status(database, 2)).status == "needs_reconnect"


async def test_an_unauthenticated_error_without_a_401_is_not_a_rejection_in_run(monkeypatch):
    main, database = await setup_app(monkeypatch, "g22")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        def flaky(*a):
            raise GarminConnectAuthenticationError("Failed to retrieve social profile")
        g.get_activities = flaky
        with pytest.raises(garmin_auth.GarminUnavailable):
            await garmin_auth.run(g, "get_activities", 0, 1)
    assert (await _status(database, 2)).status == "ok"


# ---- I2: a validation error must not echo the password ----------------------

async def test_a_422_never_echoes_the_password(monkeypatch):
    main, database = await setup_app(monkeypatch, "g23")
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/garmin/connect", json={"password": PASSWORD})
    assert r.status_code == 422
    assert PASSWORD not in r.text
    assert any(e["loc"][-1] == "email" for e in r.json()["detail"])


# ---- I3: sign-in attempts are rate limited and bounded ----------------------

async def test_the_sixth_attempt_in_an_hour_is_429_and_never_reaches_garmin(monkeypatch):
    main, database = await setup_app(monkeypatch, "g24")
    FakeGarmin.reject = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        codes = [(await c.post("/api/garmin/connect",
                               json={"email": "b@g.co", "password": PASSWORD})).status_code
                 for _ in range(6)]
    assert codes == [400] * 5 + [429]
    assert len(FakeGarmin.instances) == 5


async def test_the_global_cap_holds_across_users(monkeypatch):
    main, database = await setup_app(monkeypatch, "g25")
    import garmin_auth
    FakeGarmin.reject = True
    garmin_auth.CONNECT_GLOBAL_PER_HOUR = 7
    c1 = await session_cookie(database, 1)
    c2 = await session_cookie(database, 2)
    codes = []
    for i in range(9):
        async with client(main, c1 if i % 2 else c2) as c:
            codes.append((await c.post("/api/garmin/connect",
                          json={"email": "x@g.co", "password": PASSWORD})).status_code)
    assert codes == [400] * 7 + [429] * 2
    assert len(FakeGarmin.instances) == 7


async def test_at_most_two_logins_run_at_once(monkeypatch):
    main, database = await setup_app(monkeypatch, "g26")
    import garmin_auth
    FakeGarmin.mfa = True
    lock, state = threading.Lock(), {"now": 0, "max": 0}
    release = threading.Event()

    def slow():
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        release.wait(5)
        with lock:
            state["now"] -= 1
    FakeGarmin.in_login = slow
    tasks = [asyncio.create_task(garmin_auth.start_login(uid, "x@g.co", PASSWORD))
             for uid in (11, 12, 13, 14, 15)]
    await asyncio.sleep(0.5)
    assert state["now"] == 2          # the other three are waiting, not running
    release.set()
    assert set(await asyncio.gather(*tasks)) == {"needs_mfa"}
    assert state["max"] == 2


# ---- I4: no password copy lingers in the pending-MFA object ------------------

async def test_no_password_copy_survives_in_the_pending_client(monkeypatch):
    main, database = await setup_app(monkeypatch, "g27")
    import garmin_auth
    FakeGarmin.mfa = True
    FakeGarmin.widget = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
        assert r.json() == {"status": "needs_mfa"}
        g = garmin_auth._pending_mfa[2][0]
        assert PASSWORD not in repr(vars(g.client)) and PASSWORD not in repr(vars(g))
        # the widget MFA step still needs the page it was sent (CSRF token): kept
        assert g.client._widget_last_resp.text == "<csrf>"
        # an attribute we do not know by name is found by what it contains
        FakeGarmin.widget = False
        g.client.__dict__["_some_other_cache"] = Resp(f"password={PASSWORD}")
        garmin_auth._scrub(g, PASSWORD)
        assert PASSWORD not in repr(vars(g.client))


# ---- M1: three code attempts per pending sign-in -----------------------------

async def test_three_wrong_codes_drop_the_pending_login(monkeypatch):
    main, database = await setup_app(monkeypatch, "g28")
    import garmin_auth
    FakeGarmin.mfa = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
        bad = []
        for i in range(3):
            bad.append(await c.post("/api/garmin/mfa", json={"code": "000000"}))
            assert (2 in garmin_auth._pending_mfa) == (i < 2)   # kept for 2 retries
        assert [b.status_code for b in bad] == [400, 400, 400]
        assert 2 not in garmin_auth._pending_mfa
        # the right code now has nothing to complete
        late = await c.post("/api/garmin/mfa", json={"code": "123456"})
    assert late.status_code == 400 and "again" in late.json()["detail"].lower()


async def test_a_wrong_code_then_the_right_one_still_connects(monkeypatch):
    main, database = await setup_app(monkeypatch, "g29")
    FakeGarmin.mfa = True
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        await c.post("/api/garmin/connect", json={"email": "b@g.co", "password": PASSWORD})
        assert (await c.post("/api/garmin/mfa", json={"code": "000000"})).status_code == 400
        good = await c.post("/api/garmin/mfa", json={"code": "123456"})
    assert good.json() == {"status": "connected"}


# ---- M3: tokens refreshed by a call that then fails are still saved ----------

async def test_rotated_tokens_are_saved_even_when_the_call_fails(monkeypatch):
    main, database = await setup_app(monkeypatch, "g30")
    import garmin_auth
    await _seed(database, 2, "B" * 600)
    with tenancy.user_context(2):
        g = await garmin_auth.get_garmin()
        def refresh_then_fail(*a):
            g.client.tokens = "R" * 600
            raise RuntimeError("boom after refresh")
        g.get_activities = refresh_then_fail
        with pytest.raises(RuntimeError, match="boom"):
            await garmin_auth.run(g, "get_activities", 0, 1)
    assert (await _status(database, 2)).data == {"tokens": "R" * 600}
