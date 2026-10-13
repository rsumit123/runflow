"""Fix round for per-user sync and the connector: timeouts, call_api limits,
sanitised errors, GET handling, log redaction."""
import asyncio
import logging

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

import tenancy
from tests.multiuser_fixtures import client, session_cookie, setup_app

pytestmark = pytest.mark.no_user


async def _creds(database, **statuses):
    """statuses: e.g. garmin_1="ok", strava_2="needs_reconnect"."""
    import credentials
    async with database.async_session() as s:
        for key, status in statuses.items():
            provider, uid = key.split("_")
            await credentials.put(s, int(uid), provider, {"tokens": "x" * 600,
                                  "access_token": "a", "refresh_token": "r"})
            if status != "ok":
                await credentials.set_status(s, int(uid), provider, status)
        await s.commit()


async def _status(main, database, uid):
    cookie = await session_cookie(database, uid)
    async with client(main, cookie) as c:
        return (await c.get("/api/sync/status")).json()


# --- IMPORTANT 1 -----------------------------------------------------------

async def test_a_stuck_user_times_out_and_the_next_user_still_syncs(monkeypatch):
    main, database = await setup_app(monkeypatch, "f1")
    await _creds(database, garmin_1="ok", garmin_2="ok")
    monkeypatch.setattr(main, "AUTO_SYNC_USER_TIMEOUT_SEC", 0.05)
    seen = []

    async def fake_once(user_id, has_garmin):
        if user_id == 1:
            await asyncio.Event().wait()  # blocks forever
        seen.append(user_id)
    monkeypatch.setattr(main, "_auto_sync_once", fake_once)
    await asyncio.wait_for(main._auto_sync_pass(), timeout=5)
    assert seen == [2]
    assert main._last_auto_sync[1]["error"] == "Sync took too long; it will continue next pass"


async def test_next_pass_is_scheduled_from_its_start(monkeypatch):
    main, _ = await setup_app(monkeypatch, "f1b")
    assert main._sleep_for(100) == main.AUTO_SYNC_INTERVAL_SEC - 100
    # An overrun still pauses, so passes can never run back to back.
    assert main._sleep_for(main.AUTO_SYNC_INTERVAL_SEC + 5) == main.AUTO_SYNC_MIN_PAUSE_SEC == 300


# --- IMPORTANT 2 -----------------------------------------------------------

async def test_call_api_refuses_account_routes(monkeypatch):
    main, database = await setup_app(monkeypatch, "f2")
    with tenancy.user_context(1):
        for path in ("/api/admin/invites", "/api/auth/me", "/api/connector",
                     "/api/strava/status", "/api/garmin/status", "/api/webhook"):
            out = await main.mcp_server.call_api(path=path)
            assert out.startswith(f"No GET route '{path}'"), (path, out[:200])
        ok = await main.mcp_server.call_api(path="/api/activities")
        assert not ok.startswith("No GET route"), ok[:200]


# --- MINOR 4 ---------------------------------------------------------------

async def test_arbitrary_exception_text_never_reaches_sync_status(monkeypatch):
    main, database = await setup_app(monkeypatch, "f4")
    await _creds(database, garmin_1="ok")

    async def fake_once(user_id, has_garmin):
        raise RuntimeError("fetch https://x/?token=SECRETFRAG failed")
    monkeypatch.setattr(main, "_auto_sync_once", fake_once)
    await main._auto_sync_pass()
    st = await _status(main, database, 1)
    assert "SECRETFRAG" not in str(st) and st["error"] == "RuntimeError"


async def test_garmin_access_error_text_is_kept(monkeypatch):
    main, database = await setup_app(monkeypatch, "f4b")
    await _creds(database, garmin_1="ok")
    import garmin_auth

    async def fake_once(user_id, has_garmin):
        raise garmin_auth.GarminUnavailable("Garmin is unavailable right now")
    monkeypatch.setattr(main, "_auto_sync_once", fake_once)
    await main._auto_sync_pass()
    assert main._last_auto_sync[1]["error"] == "Garmin is unavailable right now"


async def test_garmin_fetch_failure_detail_is_fixed(monkeypatch):
    main, database = await setup_app(monkeypatch, "f4c")
    from fastapi import HTTPException

    class G:
        async def get_recent_running(self, limit, start):
            raise ValueError("GET https://x/?token=SECRETFRAG")
    monkeypatch.setattr(main, "garmin", G())
    with tenancy.user_context(1):
        async with database.async_session() as s:
            with pytest.raises(HTTPException) as ei:
                await main.import_garmin_sync(s)
    assert ei.value.status_code == 502 and ei.value.detail == "Garmin fetch failed"


# --- MINOR 5 and 6 ---------------------------------------------------------

async def test_weather_backfill_runs_even_when_garmin_fails(monkeypatch):
    main, database = await setup_app(monkeypatch, "f5")
    calls = []

    async def boom(session):
        raise RuntimeError("garmin down")

    async def wx(session, force=False):
        calls.append("weather")
        return {"updated": 0}
    monkeypatch.setattr(main, "import_garmin_sync", boom)
    monkeypatch.setattr(main, "_backfill_weather", wx)
    with tenancy.user_context(1):
        with pytest.raises(RuntimeError, match="garmin down"):
            await main._auto_sync_once(1, True)
    assert calls == ["weather"]


async def test_strava_only_user_is_not_reported_as_a_pull(monkeypatch):
    main, database = await setup_app(monkeypatch, "f6")

    async def wx(session, force=False):
        return {"updated": 0}
    monkeypatch.setattr(main, "_backfill_weather", wx)
    with tenancy.user_context(2):
        await main._auto_sync_once(2, False)
    st = main._last_auto_sync[2]
    assert st["imported"] == 0 and "webhook" in st["note"].lower() and st["error"] is None


async def test_users_needing_reconnect_get_weather_and_a_reconnect_error(monkeypatch):
    main, database = await setup_app(monkeypatch, "f6b")
    await _creds(database, garmin_1="needs_reconnect", strava_2="needs_reconnect")
    weather_for = []

    async def wx(session, force=False):
        weather_for.append(tenancy.current_user_id())
        return {"updated": 0}

    async def never(session):
        raise AssertionError("must not call Garmin for a user who needs to reconnect")
    monkeypatch.setattr(main, "_backfill_weather", wx)
    monkeypatch.setattr(main, "import_garmin_sync", never)
    await main._auto_sync_pass()
    assert weather_for == [1, 2]
    assert main._last_auto_sync[1]["error"] == "Reconnect Garmin in Settings"
    assert main._last_auto_sync[2]["error"] == "Reconnect Strava in Settings"


# --- MINOR 7, 9 ------------------------------------------------------------

async def _token(database, uid):
    import connector
    async with database.async_session() as s:
        raw = await connector.rotate(s, uid)
        await s.commit()
    return raw


async def _get(main, path):
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        return await asyncio.wait_for(
            c.get(path, headers={"Accept": "text/event-stream"}), timeout=5)


async def test_get_on_a_stateless_connector_is_405_and_unknown_token_404(monkeypatch):
    main, database = await setup_app(monkeypatch, "f7")
    tok = await _token(database, 1)
    async with main.mcp_server.mcp.session_manager.run():
        r = await _get(main, f"/mcp/{tok}/mcp")
        assert r.status_code == 405 and "POST" in r.headers.get("allow", "")
        assert (await _get(main, "/mcp/nope/mcp")).status_code == 404


async def test_connector_closes_websockets_and_503s_on_db_errors(monkeypatch):
    main, database = await setup_app(monkeypatch, "f9")
    import connector
    inner_called = []

    async def inner(scope, receive, send):
        inner_called.append(scope["type"])
    app = connector.ConnectorApp(inner)
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "websocket.connect"}
    await app({"type": "websocket", "path_params": {"token": "t"}}, receive, send)
    assert sent == [{"type": "websocket.close", "code": 1008}] and not inner_called

    async def broken(raw):
        raise SQLAlchemyError("db down")
    monkeypatch.setattr(connector, "user_for_token", broken)
    sent.clear()
    await app({"type": "http", "method": "POST", "path_params": {"token": "t"}},
              receive, send)
    assert sent[0]["status"] == 503 and not inner_called


# --- MINOR 8 ---------------------------------------------------------------

async def test_connector_tokens_are_redacted_from_access_logs(monkeypatch, caplog):
    main, database = await setup_app(monkeypatch, "f8")
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5", "POST", "/mcp/SECRETTOKEN123/mcp", "1.1", 200)
    text = caplog.text
    assert "SECRETTOKEN123" not in text and "/mcp/<redacted>/mcp" in text


# --- MINOR 10 --------------------------------------------------------------

async def test_warns_when_connector_urls_would_point_at_localhost(monkeypatch, caplog):
    main, database = await setup_app(monkeypatch, "f10")
    import config
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "PUBLIC_API_URL", "http://localhost:8000")
    with caplog.at_level(logging.WARNING):
        main._warn_on_dev_public_url()
    assert any("PUBLIC_API_URL" in r.message and r.levelno == logging.WARNING
               for r in caplog.records)
    caplog.clear()
    monkeypatch.setattr(config, "PUBLIC_API_URL", "https://runflow-api.skdev.one")
    with caplog.at_level(logging.WARNING):
        main._warn_on_dev_public_url()
    assert not any("PUBLIC_API_URL" in r.message for r in caplog.records)
