"""Replacing a planned workout's description.

The generator's easy-run text tells the athlete to take walk breaks rather than
shuffling slower. For an athlete whose start-stop running causes next-day pain
that is the wrong instruction, and until this endpoint existed there was no way
to change it without opening the database.
"""
import tempfile
from datetime import datetime, timedelta

import pytest


async def _app(monkeypatch, tag):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, sys, config, database
    for _m in ("main", "mcp_server", "models"):
        sys.modules.pop(_m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401 — populates Base.metadata before init_db
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)
    return main, database


async def _seed_workout(database, wid=1):
    from models import Plan, PlannedWorkout
    now = datetime.utcnow()
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1800,
                    start_date=now, goal_date=now + timedelta(days=84),
                    weeks=12, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        s.add(PlannedWorkout(
            id=wid, plan_id=plan.id, date=now + timedelta(days=1), week_number=1,
            day_type="easy", target_distance_m=3000, pace_low_sec=435,
            pace_high_sec=450, hr_ceiling=160, title="Easy run",
            description="Keep it conversational. If your HR climbs, take walk "
                        "breaks rather than shuffling slower."))
        await s.commit()
    return wid


@pytest.mark.asyncio
async def test_note_replaces_the_generated_description(monkeypatch):
    main, database = await _app(monkeypatch, "pn")
    wid = await _seed_workout(database)
    from models import PlannedWorkout

    async with database.async_session() as s:
        res = await main.set_planned_workout_note(
            wid, main.NoteRequest(note="Continuous only. Cap at 22 min."), s)

    assert res["description"] == "Continuous only. Cap at 22 min."
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, wid)
        assert w.description == "Continuous only. Cap at 22 min."
        assert "walk breaks" not in w.description


@pytest.mark.asyncio
async def test_clearing_the_note_empties_the_description(monkeypatch):
    main, database = await _app(monkeypatch, "pn2")
    wid = await _seed_workout(database)

    async with database.async_session() as s:
        res = await main.set_planned_workout_note(wid, main.NoteRequest(note=""), s)

    assert res["description"] in (None, "")


@pytest.mark.asyncio
async def test_note_on_a_missing_workout_is_a_404(monkeypatch):
    main, database = await _app(monkeypatch, "pn3")
    from fastapi import HTTPException

    async with database.async_session() as s:
        with pytest.raises(HTTPException) as exc:
            await main.set_planned_workout_note(9999, main.NoteRequest(note="x"), s)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_an_overlong_note_is_rejected_not_truncated(monkeypatch):
    """Silently truncating would lose the end of what the athlete wrote."""
    main, database = await _app(monkeypatch, "pn4")
    wid = await _seed_workout(database)
    import config
    from fastapi import HTTPException
    from models import PlannedWorkout

    async with database.async_session() as s:
        with pytest.raises(HTTPException) as exc:
            await main.set_planned_workout_note(
                wid, main.NoteRequest(note="x" * (config.NOTE_MAX_CHARS + 1)), s)
    assert exc.value.status_code == 400

    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, wid)
        assert "walk breaks" in w.description, "a rejected note must change nothing"


@pytest.mark.asyncio
async def test_the_note_does_not_touch_the_watch(monkeypatch):
    """Description is text for the athlete; the watch holds steps and paces.

    Re-pushing on a text edit would risk a Garmin failure for no benefit.
    """
    main, database = await _app(monkeypatch, "pn5")
    wid = await _seed_workout(database)
    pushed = []

    async def spy(w):
        pushed.append(w.id)
    monkeypatch.setattr(main, "_garmin_push", spy)

    async with database.async_session() as s:
        await main.set_planned_workout_note(wid, main.NoteRequest(note="Continuous."), s)

    assert pushed == [], "a note edit must not re-push to the watch"
