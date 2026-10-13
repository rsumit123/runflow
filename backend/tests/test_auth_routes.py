"""Sign-up, login, logout, me, admin invites, and request-level rules."""
import pytest

import tenancy
from tests.multiuser_fixtures import client, session_cookie, setup_app

pytestmark = pytest.mark.no_user


async def _invite(database, uses=1):
    import auth
    async with database.async_session() as s:
        code = await auth.create_invite(s, created_by=1, uses=uses)
        await s.commit()
    return code


async def test_signup_with_an_invite_logs_you_in(monkeypatch):
    main, database = await setup_app(monkeypatch, "r1")
    code = await _invite(database)
    async with client(main) as c:
        r = await c.post("/api/auth/signup", json={"email": "New@X.co",
                         "password": "a long password", "invite_code": code})
        assert r.status_code == 200, r.text
        cookie = r.headers["set-cookie"].lower()
        assert "rf_session=" in cookie and "httponly" in cookie
        assert "samesite=lax" in cookie and "secure" in cookie
        assert "domain=" not in cookie  # host-only: no other subdomain receives it
        me = await c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "new@x.co"


async def test_signup_with_a_bad_invite_is_refused(monkeypatch):
    main, database = await setup_app(monkeypatch, "r2")
    async with client(main) as c:
        r = await c.post("/api/auth/signup", json={"email": "n@x.co",
                         "password": "a long password", "invite_code": "nope"})
    assert r.status_code == 400 and "invite" in r.json()["detail"].lower()


async def test_login_errors_do_not_say_which_part_was_wrong(monkeypatch):
    main, database = await setup_app(monkeypatch, "r3")
    async with client(main) as c:
        wrong_pw = await c.post("/api/auth/login",
                                json={"email": "a@example.com", "password": "nope nope nope"})
        no_user = await c.post("/api/auth/login",
                               json={"email": "ghost@example.com", "password": "nope nope nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


async def test_login_is_rate_limited(monkeypatch):
    main, database = await setup_app(monkeypatch, "r4")
    import auth_routes
    auth_routes.rate_limiter = __import__("auth").LoginRateLimiter()
    async with client(main) as c:
        codes = [(await c.post("/api/auth/login", json={"email": "a@example.com",
                  "password": "wrong password!"})).status_code for _ in range(6)]
        good = await c.post("/api/auth/login",
                            json={"email": "a@example.com", "password": "a long password"})
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert good.status_code == 429  # still locked: the right password does not bypass it


async def test_login_then_logout(monkeypatch):
    main, database = await setup_app(monkeypatch, "r5")
    async with client(main) as c:
        r = await c.post("/api/auth/login",
                         json={"email": "a@example.com", "password": "a long password"})
        assert r.status_code == 200
        assert (await c.get("/api/auth/me")).status_code == 200
        assert (await c.post("/api/auth/logout")).status_code == 200
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_cross_origin_writes_are_refused(monkeypatch):
    main, database = await setup_app(monkeypatch, "r6")
    async with client(main, origin="https://evil.example") as c:
        r = await c.post("/api/auth/login",
                         json={"email": "a@example.com", "password": "a long password"})
    assert r.status_code == 403


async def test_only_admins_create_invites_and_codes_are_shown_once(monkeypatch):
    main, database = await setup_app(monkeypatch, "r7")
    b = await session_cookie(database, 2)
    a = await session_cookie(database, 1)
    async with client(main, b) as c:
        assert (await c.post("/api/admin/invites", json={"uses": 1})).status_code == 403
    async with client(main, a) as c:
        r = await c.post("/api/admin/invites", json={"uses": 2, "expires_days": 7})
        assert r.status_code == 200
        code = r.json()["code"]
        listing = await c.get("/api/admin/invites")
    assert listing.status_code == 200 and code not in listing.text
    assert listing.json()[0]["uses_left"] == 2


async def test_in_process_calls_run_as_the_user_already_in_context(monkeypatch):
    """call_api goes through ASGITransport in-process; it must act as its caller."""
    main, database = await setup_app(monkeypatch, "r8")
    async with client(main) as c:
        assert (await c.get("/api/auth/me")).status_code == 401
        with tenancy.user_context(2):
            r = await c.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["id"] == 2


async def test_import_jobs_are_private_to_their_owner(monkeypatch):
    main, database = await setup_app(monkeypatch, "r9")
    main.import_progress["job-a"] = {"user_id": 1, "status": "running",
                                     "imported": 3, "total": 10}
    a = await session_cookie(database, 1)
    b = await session_cookie(database, 2)
    async with client(main, b) as c:
        assert (await c.get("/api/import/status", params={"job_id": "job-a"})).status_code == 404
    async with client(main, a) as c:
        assert (await c.get("/api/import/status", params={"job_id": "job-a"})).status_code == 200


async def test_bulk_import_reads_the_server_disk_so_it_is_admin_only(monkeypatch):
    main, database = await setup_app(monkeypatch, "r10")
    b = await session_cookie(database, 2)
    async with client(main, b) as c:
        r = await c.post("/api/import/bulk", json={"directory": "/etc"})
    assert r.status_code == 403


async def test_duplicate_session_cookies_cannot_outlive_logout(monkeypatch):
    """The middleware and logout must agree on which cookie counts, and logout
    must end every session the header names."""
    main, database = await setup_app(monkeypatch, "r11")
    valid = await session_cookie(database, 1)
    dup = {"Cookie": f"rf_session={valid}; rf_session=junk"}
    async with client(main) as c:
        assert (await c.post("/api/auth/logout", headers=dup)).status_code == 200
    async with client(main, valid) as c:
        assert (await c.get("/api/auth/me")).status_code == 401
    async with client(main) as c:
        assert (await c.get("/api/auth/me", headers=dup)).status_code == 401


async def test_websocket_scopes_under_api_are_closed(monkeypatch):
    main, database = await setup_app(monkeypatch, "r12")
    valid = await session_cookie(database, 1)
    for headers in ([], [(b"cookie", f"rf_session={valid}".encode())]):
        scope = {"type": "websocket", "path": "/api/anything", "raw_path": b"/api/anything",
                 "root_path": "", "scheme": "wss", "query_string": b"", "headers": headers,
                 "subprotocols": [], "server": ("test", 443), "client": ("1.2.3.4", 1)}
        sent = []
        queue = [{"type": "websocket.connect"}]

        async def receive():
            return queue.pop(0) if queue else {"type": "websocket.disconnect", "code": 1000}

        async def send(msg):
            sent.append(msg)

        await main.app(scope, receive, send)
        closes = [m for m in sent if m["type"] == "websocket.close"]
        assert closes and closes[0]["code"] == 1008, sent
        assert not any(m["type"] == "websocket.accept" for m in sent)


async def test_a_database_error_while_resolving_the_session_is_a_503(monkeypatch):
    main, database = await setup_app(monkeypatch, "r13")
    import auth
    from sqlalchemy.exc import OperationalError

    async def boom(session, raw):
        raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(auth, "resolve_session", boom)
    async with client(main, "anything") as c:
        r = await c.get("/api/activities")
    assert r.status_code == 503 and r.json() == {"detail": "Service temporarily unavailable"}
