"""Per-user connector URLs over the real MCP transport, and per-user sync."""
import json
import socket

import httpx
import pytest

import tenancy
from tests.multiuser_fixtures import (A_ACTIVITY_ID, B_ACTIVITY_ID, MARK, client, seed_user_a, seed_user_b,
                                      session_cookie, setup_app)

pytestmark = pytest.mark.no_user


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


async def _token(database, user_id):
    import connector
    async with database.async_session() as s:
        raw = await connector.rotate(s, user_id)
        await s.commit()
    return raw


async def _call_tool(main, token, name, arguments=None):
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        r = await c.post(f"/mcp/{token}/mcp", headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": name, "arguments": arguments or {}}})
    if r.status_code != 200:
        return r.status_code, r.text
    payload = r.text
    if "data:" in payload:  # SSE framing
        payload = next(line[5:].strip() for line in payload.splitlines()
                       if line.startswith("data:"))
    body = json.loads(payload)
    return r.status_code, json.dumps(body)


async def test_each_connector_url_sees_only_its_owners_runs(monkeypatch):
    main, database = await setup_app(monkeypatch, "m1")
    await seed_user_a(database)
    await seed_user_b(database)
    tok_a, tok_b = await _token(database, 1), await _token(database, 2)
    async with main.mcp_server.mcp.session_manager.run():
        sa, a = await _call_tool(main, tok_a, "list_recent_runs")
        sb, b = await _call_tool(main, tok_b, "list_recent_runs")
        sa2, a_detail_as_b = await _call_tool(main, tok_b, "get_run_detail",
                                              {"run_id": A_ACTIVITY_ID})
    assert sa == sb == 200
    # list_recent_runs prints ids, not names, so assert on ids as well as MARK.
    assert str(A_ACTIVITY_ID) in a and str(B_ACTIVITY_ID) not in a
    assert str(B_ACTIVITY_ID) in b
    assert MARK not in b and str(A_ACTIVITY_ID) not in b and "9003" not in b
    assert sa2 == 200 and MARK not in a_detail_as_b
    assert "5.00km" not in a_detail_as_b and "has note" not in a_detail_as_b


async def test_an_unknown_token_is_a_404(monkeypatch):
    main, database = await setup_app(monkeypatch, "m2")
    async with main.mcp_server.mcp.session_manager.run():
        status, _ = await _call_tool(main, "not-a-real-token", "list_recent_runs")
    assert status == 404


async def test_rotating_the_url_kills_the_old_one(monkeypatch):
    main, database = await setup_app(monkeypatch, "m3")
    old = await _token(database, 2)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        assert (await c.get("/api/connector")).json()["exists"] is True
        r = await c.post("/api/connector/rotate")
    new_url = r.json()["url"]
    assert new_url.endswith("/mcp") and old not in new_url
    new = new_url.rstrip("/").split("/")[-2]
    async with main.mcp_server.mcp.session_manager.run():
        assert (await _call_tool(main, old, "list_recent_runs"))[0] == 404
        assert (await _call_tool(main, new, "list_recent_runs"))[0] == 200


async def test_read_tools_called_directly_as_b_never_show_a(monkeypatch):
    main, database = await setup_app(monkeypatch, "m4")
    await seed_user_a(database)
    await seed_user_b(database)
    ms = main.mcp_server
    calls = [ms.list_recent_runs(), ms.get_records(), ms.get_training_context(),
             ms.get_weekly_volume(), ms.get_aerobic_trend(), ms.get_fitness_model(),
             ms.get_plan_status(), ms.get_run_detail(run_id=A_ACTIVITY_ID),
             ms.review_run(run_id=A_ACTIVITY_ID)]
    with tenancy.user_context(2):
        outs = [await c for c in calls]
    leaked = [o[:120] for o in outs if MARK in o]
    assert not leaked, leaked


async def test_auto_sync_runs_each_user_in_their_own_context(monkeypatch):
    main, database = await setup_app(monkeypatch, "m5")
    import credentials
    async with database.async_session() as s:
        await credentials.put(s, 1, "garmin", {"tokens": "A" * 600})
        await credentials.put(s, 2, "strava", {"access_token": "a", "refresh_token": "r"})
        await s.commit()
    seen = []

    async def fake_once(user_id, has_garmin):
        seen.append((user_id, has_garmin, tenancy.current_user_id()))
        if user_id == 1:
            raise RuntimeError("garmin down")
    monkeypatch.setattr(main, "_auto_sync_once", fake_once)
    await main._auto_sync_pass()
    assert seen == [(1, True, 1), (2, False, 2)]  # user 1's failure did not stop user 2
    # only the exception type is stored, never its text
    assert main._last_auto_sync[1]["error"] == "RuntimeError"


async def test_sync_status_is_per_user(monkeypatch):
    main, database = await setup_app(monkeypatch, "m6")
    main._last_auto_sync[1] = {"at": "2026-10-13T00:00:00", "imported": 9, "error": None}
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.get("/api/sync/status")
    assert r.status_code == 200 and r.json()["imported"] == 0
