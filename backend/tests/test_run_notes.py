"""Notes on a run — the one write this connector allows.

Context the sensors cannot capture ("ran on the stadium track", "hip felt
tight") is exactly what makes a later read correct, so it has to be writable
from wherever the athlete is talking, not just the web UI.
"""
import tempfile
from datetime import datetime

import pytest


async def _app(monkeypatch, tag, secret="testsecret"):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", secret)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    async with database.async_session() as s:
        s.add(Activity(id=1, name="Track run", distance=2510.0, moving_time=1091,
                       elapsed_time=1091, start_date=datetime(2026, 10, 9, 12, 7),
                       average_speed=2.3, average_heartrate=176.0,
                       max_heartrate=187.0, total_elevation_gain=0.0,
                       elev_high=174.4, elev_low=172.6))
        await s.commit()
    return main, mcp_server, database


@pytest.mark.asyncio
async def test_the_notes_column_exists_after_migration(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "na")
    from models import Activity

    async with database.async_session() as s:
        a = await s.get(Activity, 1)
        assert a.notes is None


@pytest.mark.asyncio
async def test_set_note_persists_and_is_returned_by_the_endpoint(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "nb")

    async with database.async_session() as s:
        res = await main.set_activity_note(
            1, main.NoteRequest(note="Flat stadium track, first day open."), s
        )
        assert res["note"] == "Flat stadium track, first day open."

    async with database.async_session() as s:
        got = await main.get_activity(1, s)
        assert got["notes"] == "Flat stadium track, first day open."


@pytest.mark.asyncio
async def test_set_note_on_a_missing_run_is_a_404(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "nc")
    from fastapi import HTTPException

    async with database.async_session() as s:
        with pytest.raises(HTTPException):
            await main.set_activity_note(9999, main.NoteRequest(note="x"), s)


@pytest.mark.asyncio
async def test_clearing_a_note_sets_it_back_to_empty(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "nd")

    async with database.async_session() as s:
        await main.set_activity_note(1, main.NoteRequest(note="temporary"), s)
    async with database.async_session() as s:
        res = await main.set_activity_note(1, main.NoteRequest(note=""), s)
        assert res["note"] in (None, "")


@pytest.mark.asyncio
async def test_mcp_tool_writes_a_note(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ne")

    out = await mcp_server.set_run_note(run_id=1, note="Stadium track, flat.")

    assert "saved" in out.lower()
    from models import Activity
    async with database.async_session() as s:
        a = await s.get(Activity, 1)
        assert a.notes == "Stadium track, flat."


@pytest.mark.asyncio
async def test_mcp_tool_rejects_an_overlong_note(monkeypatch):
    """A note is context, not a dumping ground — cap it rather than bloat rows."""
    main, mcp_server, database = await _app(monkeypatch, "nf")

    out = await mcp_server.set_run_note(run_id=1, note="x" * 3000)

    assert "too long" in out.lower()
    from models import Activity
    async with database.async_session() as s:
        a = await s.get(Activity, 1)
        assert a.notes is None


@pytest.mark.asyncio
async def test_mcp_tool_on_a_missing_run_is_readable(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ng")

    out = await mcp_server.set_run_note(run_id=9999, note="x")

    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_run_detail_shows_the_note(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "nh")
    await mcp_server.set_run_note(run_id=1, note="Stadium track, flat, humid.")

    out = await mcp_server.get_run_detail(run_id=1)

    assert "Stadium track, flat, humid." in out


@pytest.mark.asyncio
async def test_list_recent_runs_flags_which_runs_have_notes(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ni")
    await mcp_server.set_run_note(run_id=1, note="Track.")

    out = await mcp_server.list_recent_runs(limit=5)

    assert "note" in out.lower()
