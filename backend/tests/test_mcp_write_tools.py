"""The nine write tools.

Each is a thin wrapper over an endpoint that already holds the logic, so these
tests are about the wrapper's job: validating arguments, rendering what
actually changed, and never reporting a plan change and a watch change as the
same thing.
"""
import tempfile
from datetime import datetime, timedelta

import pytest


async def _app(monkeypatch, tag):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, sys, config, database
    for _m in ("main", "mcp_server", "models"):
        sys.modules.pop(_m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401 — populates Base.metadata before init_db
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)
    return main, mcp_server, database


async def _seed_plan(database, *, start_offset_days=0, n_sessions=3,
                     on_watch=False, with_steps=True):
    """An active 12-week plan with n_sessions upcoming workouts, ids 1..n.

    on_watch gives each a garmin_workout_id so unpush/move have something to
    take off the watch. with_steps controls whether they are pushable at all.
    """
    from models import Plan, PlannedWorkout
    now = datetime.utcnow()
    start = now + timedelta(days=start_offset_days)
    steps = {"steps": [{"type": "run", "end_kind": "distance",
                        "end_value": 3000, "target_kind": "pace",
                        "pace_low_sec": 435, "pace_high_sec": 450}]}
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1800,
                    start_date=start, goal_date=start + timedelta(days=84),
                    weeks=12, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        for i in range(1, n_sessions + 1):
            s.add(PlannedWorkout(
                id=i, plan_id=plan.id, date=start + timedelta(days=i),
                week_number=1, day_type="easy" if i < n_sessions else "long",
                target_distance_m=3000 + 500 * i, pace_low_sec=435,
                pace_high_sec=450, hr_ceiling=160, title=f"Session {i}",
                description="Keep it conversational. Take walk breaks.",
                structure=steps if with_steps else None,
                garmin_workout_id=(1000 + i) if on_watch else None))
        await s.commit()
        return plan.id


@pytest.mark.asyncio
async def test_set_workout_note_replaces_the_description(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn")
    await _seed_plan(database)
    from models import PlannedWorkout

    out = await mcp_server.set_workout_note(
        workout_id=1, note="Continuous only — no walk breaks.")

    assert "Continuous only" in out
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w.description == "Continuous only — no walk breaks."


@pytest.mark.asyncio
async def test_set_workout_note_on_a_missing_workout_is_readable(monkeypatch):
    """The endpoint raises HTTPException; a bare except would print its repr."""
    main, mcp_server, database = await _app(monkeypatch, "wn2")
    await _seed_plan(database)

    out = await mcp_server.set_workout_note(workout_id=9999, note="x")

    assert "not found" in out.lower()
    assert "HTTPException" not in out, "render the detail, not the exception repr"
    assert "Traceback" not in out
    # str(HTTPException) is "404: Workout not found" — no class name in it, so
    # the assertion above alone does not catch a bare `except Exception`.
    assert "404" not in out, "a status code leaking in means detail was not rendered"


@pytest.mark.asyncio
async def test_set_workout_note_rejects_an_overlong_note(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn3")
    await _seed_plan(database)
    import config
    from models import PlannedWorkout

    out = await mcp_server.set_workout_note(
        workout_id=1, note="x" * (config.NOTE_MAX_CHARS + 1))

    assert "too long" in out.lower()
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert "walk breaks" in w.description, "nothing should have changed"


@pytest.mark.asyncio
async def test_set_workout_note_clearing_reads_as_cleared(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn4")
    await _seed_plan(database)

    out = await mcp_server.set_workout_note(workout_id=1, note="")

    assert "clear" in out.lower(), f"an empty note should read as cleared: {out}"
