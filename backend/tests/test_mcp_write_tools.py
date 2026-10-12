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
    start = (now + timedelta(days=start_offset_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
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


def _no_raw_http(out: str) -> bool:
    """True when no raw HTTPException str() leaked into the response.

    str(HTTPException(404, "Workout not found")) is '404: Workout not found',
    so a class-name check cannot catch this — the status code and colon can.
    """
    return not any(f"{code}: " in out for code in (400, 404, 422, 500, 502))


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
    assert "Traceback" not in out
    # str(HTTPException) is "404: Workout not found" — no class name in it, so a
    # class-name check cannot catch a bare `except Exception`.
    assert _no_raw_http(out), "a status code leaking in means detail was not rendered"


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


@pytest.mark.asyncio
async def test_move_workout_reports_the_new_date_and_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv")
    await _seed_plan(database, on_watch=True)
    pushed = []

    async def fake_push(w):
        pushed.append(w.id)
        w.garmin_workout_id = 2001
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    target = (datetime.utcnow() + timedelta(days=9)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    assert target in out
    assert pushed == [1], "a future move must re-push to the watch"
    assert "watch" in out.lower(), f"the watch outcome must be stated: {out}"
    assert "not" not in out.lower(), f"a successful move must not read as a failure: {out}"
    assert "Session 1" in out


@pytest.mark.asyncio
async def test_move_workout_says_the_watch_was_not_updated_when_garmin_fails(monkeypatch):
    """The move is saved and the watch is stale. Those must not read the same.

    If the tool says "moved to Friday" while Garmin still holds Thursday, the
    athlete runs the wrong session on the wrong day. Note the workout KEEPS its
    old garmin_workout_id here (the endpoint does not clear it on a failed
    re-push), so a clause keyed on the id alone would say "on the watch".
    """
    main, mcp_server, database = await _app(monkeypatch, "mv2")
    await _seed_plan(database, on_watch=True)
    from models import PlannedWorkout

    async def broken(w):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main, "_garmin_push", broken)

    target = (datetime.utcnow() + timedelta(days=9)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w.date.date().isoformat() == target, "the move itself must persist"
        assert w.garmin_workout_id == 1001, "precondition: the stale id is still set"

    low = out.lower()
    assert "not" in low and "watch" in low, (
        f"must say the watch was NOT updated, got: {out}"
    )
    assert target in out, "the saved move should still be reported"
    assert "old" in low or "previous" in low, f"must say the watch holds the old version: {out}"


@pytest.mark.asyncio
async def test_move_workout_into_the_past_is_not_called_a_failure(monkeypatch):
    """Deliberately taken off the watch is a different fact from a failed push."""
    main, mcp_server, database = await _app(monkeypatch, "mv2b")
    await _seed_plan(database, on_watch=True)
    removed = []

    async def fake_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    target = (datetime.utcnow() - timedelta(days=3)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    assert removed == [1001]
    low = out.lower()
    assert "past" in low, f"the reason must be named: {out}"
    assert "failed" not in low and "still" not in low, f"not a failure: {out}"


@pytest.mark.asyncio
async def test_move_workout_surfaces_the_back_to_back_warning(monkeypatch):
    """The endpoint warns without blocking; the tool must not swallow it."""
    main, mcp_server, database = await _app(monkeypatch, "mv3")
    await _seed_plan(database, n_sessions=3)

    async def fake_push(w):
        w.garmin_workout_id = 2002
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    from models import PlannedWorkout
    async with database.async_session() as s:
        hard = await s.get(PlannedWorkout, 3)       # day_type "long"
        hard_date = hard.date.date()

    # Move session 1 to the day before the long run, and make it hard too.
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        w.day_type = "quality"
        await s.commit()

    out = await mcp_server.move_workout(
        workout_id=1, date=(hard_date - timedelta(days=1)).isoformat())

    assert "hard" in out.lower(), f"the back-to-back warning must appear: {out}"


@pytest.mark.asyncio
async def test_move_workout_rejects_a_malformed_date(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv4")
    await _seed_plan(database)

    out = await mcp_server.move_workout(workout_id=1, date="next Tuesday")

    assert "yyyy-mm-dd" in out.lower() or "iso" in out.lower()
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_move_workout_on_a_missing_workout_is_readable(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv5")
    await _seed_plan(database)

    out = await mcp_server.move_workout(
        workout_id=9999, date=(datetime.utcnow() + timedelta(days=3)).date().isoformat())

    assert "not found" in out.lower()
    assert _no_raw_http(out)


def test_watch_clause_three_branches():
    import mcp_server
    wc = mcp_server._watch_clause

    on = wc(garmin_workout_id=7, eligible=True, push_ok=True).lower()
    assert "updated" in on and "watch" in on and "not" not in on

    # Eligible, push failed, but a STALE id is still set: must read as failure.
    stale = wc(garmin_workout_id=7, eligible=True, push_ok=False).lower()
    assert "not" in stale and "watch" in stale and "old" in stale

    # Eligible, no id, push outcome unknown: still a failure, not "no need".
    missing = wc(garmin_workout_id=None, eligible=True).lower()
    assert "not" in missing and "old" not in missing and "need" not in missing

    # Not eligible: not on the watch, and that is fine, with the reason.
    for reason in ("it is in the past", "it is a rest day", "it has no structured steps"):
        off = wc(garmin_workout_id=None, eligible=False, reason=reason).lower()
        assert reason in off and "need" in off and "failed" not in off
