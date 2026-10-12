"""Applying a suggestion must keep the watch honest.

apply_plan_suggestion changed the plan and never touched the watch, while
apply_plan_calibration re-pushed. A suggestion that caps a long run from 6.5 km
to 5 km therefore left the watch holding 6.5 km.

But a plain re-push would NOT fix that: _garmin_push uploads w.structure (plus
title and date), and no suggestion rewrites structure. Re-pushing a capped
workout re-sends the old 6.5 km steps; re-pushing a soften_quality swap sends
interval steps under an easy-run label. So a session whose structure went stale
comes OFF the watch instead, and the athlete is told.
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


_LONG_STEPS = {"steps": [{"type": "run", "end_kind": "distance",
                          "end_value": 6500, "target_kind": "pace",
                          "pace_low_sec": 435, "pace_high_sec": 450}]}


async def _seed_cap_fixture(database, *, big_gid, act_id):
    """Mirrors tests/test_plan_suggestions_endpoint.py: a completed 4 km long,
    a MISSED long (this is what triggers cap_long_run), and an oversized
    upcoming 6.5 km long. Returns the oversized workout's id."""
    from models import Plan, PlannedWorkout, Activity
    now = datetime.utcnow()
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=now - timedelta(days=14),
                    goal_date=now + timedelta(days=42),
                    weeks=8, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=8),
                             week_number=1, day_type="long",
                             target_distance_m=4000, hr_ceiling=165,
                             title="Long", description="x",
                             structure=_LONG_STEPS))
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=3),
                             week_number=2, day_type="long",
                             target_distance_m=5000, hr_ceiling=165,
                             title="Long", description="x"))   # missed
        big = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=4),
                             week_number=3, day_type="long",
                             target_distance_m=6500, hr_ceiling=165,
                             title="Long", description="x",
                             structure=_LONG_STEPS, garmin_workout_id=big_gid)
        s.add(big)
        s.add(Activity(id=act_id, name="Run", distance=4000.0,
                       start_date=now - timedelta(days=8),
                       average_speed=1000.0 / 430, average_heartrate=150.0))
        await s.commit()
        return big.id


async def _apply_cap(main, database):
    async with database.async_session() as s:
        sugg = (await main.get_plan_suggestions(s))["suggestions"]
        cap = next((x for x in sugg if x["type"] == "cap_long_run"), None)
        assert cap is not None, f"fixture should produce a cap suggestion: {sugg}"
        return await main.apply_plan_suggestion(
            main.SuggestionApplyRequest(id=cap["id"]), s)


def _spies(monkeypatch, main):
    pushed, removed = [], []

    async def spy_push(w):
        pushed.append(w.id)

    async def spy_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main, "_garmin_push", spy_push)
    monkeypatch.setattr(main.garmin, "remove_workout", spy_remove)
    return pushed, removed


@pytest.mark.asyncio
async def test_capping_a_long_run_takes_the_stale_6_5km_off_the_watch(monkeypatch):
    """The reported bug. Re-pushing would re-send the 6.5 km steps (structure is
    untouched by the cap), so the only honest outcome is to unschedule."""
    main, database = await _app(monkeypatch, "sr")
    big_id = await _seed_cap_fixture(database, big_gid=7777, act_id=500)
    pushed, removed = _spies(monkeypatch, main)

    resp = await _apply_cap(main, database)

    assert pushed == [], (
        "re-pushing a capped workout re-sends its old 6500 m steps — the watch "
        "would still hold 6.5 km while the push 'succeeded'")
    assert removed == [7777], "the stale session must come off the watch"
    from models import PlannedWorkout as PW
    async with database.async_session() as s:
        w = await s.get(PW, big_id)
        assert w.target_distance_m == 5000, "the cap itself must still apply"
        assert w.garmin_workout_id is None
    assert [u["id"] for u in resp["watch"]["unscheduled"]] == [big_id]
    assert resp["watch"]["repushed"] == [] and resp["watch"]["failed"] == []


@pytest.mark.asyncio
async def test_a_structure_changing_suggestion_unschedules_rather_than_repushing(monkeypatch):
    """A soften_quality swap leaves interval steps behind an easy-run label.

    Re-pushing that would put an interval session on the watch of an athlete
    whose problem is running easy days too hard. Taking it off the watch is the
    honest outcome; the session needs regenerating before it goes back.
    """
    main, database = await _app(monkeypatch, "sr4")
    from models import Plan, PlannedWorkout

    now = datetime.utcnow()
    intervals = {"steps": [
        {"type": "run", "end_kind": "distance", "end_value": 400,
         "target_kind": "pace", "pace_low_sec": 350, "pace_high_sec": 365},
        {"type": "recover", "end_kind": "time", "end_value": 90},
    ]}
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1800,
                    start_date=now - timedelta(days=14),
                    goal_date=now + timedelta(days=42),
                    weeks=8, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        q = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=3),
                           week_number=3, day_type="quality",
                           target_distance_m=4000, pace_low_sec=350,
                           pace_high_sec=365, hr_ceiling=175,
                           title="6x400m", description="x",
                           structure=intervals, garmin_workout_id=8888)
        s.add(q)
        await s.commit()
        q_id = q.id

    pushed, removed = _spies(monkeypatch, main)

    async with database.async_session() as s:
        out = await main._repush_after_changes(
            s, [{"workout_id": q_id, "field": "day_type", "value": "easy"}])
        await s.commit()

    assert pushed == [], (
        "a stale structure must not be pushed — that would send interval steps "
        "under an easy-run label")
    assert removed == [8888], "it must come off the watch instead"
    assert [u["id"] for u in out["unscheduled"]] == [q_id]
    from models import PlannedWorkout as PW
    async with database.async_session() as s:
        w = await s.get(PW, q_id)
        assert w.garmin_workout_id is None


@pytest.mark.asyncio
async def test_a_title_only_change_is_repushed_because_the_push_carries_the_title(monkeypatch):
    """Structure is not stale when only title/description/hr_ceiling changed, and
    the title IS part of what the watch shows — so this is the one case where a
    re-push is both safe and useful."""
    main, database = await _app(monkeypatch, "sr5")
    big_id = await _seed_cap_fixture(database, big_gid=7779, act_id=503)
    pushed, removed = _spies(monkeypatch, main)

    async with database.async_session() as s:
        out = await main._repush_after_changes(s, [
            {"workout_id": big_id, "field": "title", "value": "Renamed"},
            {"workout_id": big_id, "field": "hr_ceiling", "value": 150},
        ])

    assert pushed == [big_id]
    assert removed == []
    assert [r["id"] for r in out["repushed"]] == [big_id]
    assert out["unscheduled"] == [] and out["failed"] == []


@pytest.mark.asyncio
async def test_a_suggestion_does_not_touch_workouts_that_were_not_on_the_watch(monkeypatch):
    """The watch follower must never newly schedule anything — and has nothing
    to unschedule for a session that was never there."""
    main, database = await _app(monkeypatch, "sr2")
    big_id = await _seed_cap_fixture(database, big_gid=None, act_id=501)
    pushed, removed = _spies(monkeypatch, main)

    resp = await _apply_cap(main, database)   # asserts the suggestion exists

    from models import PlannedWorkout as PW
    async with database.async_session() as s:
        w = await s.get(PW, big_id)
        assert w.target_distance_m == 5000, "the suggestion really was applied"
        assert w.garmin_workout_id is None
    assert pushed == [], "a workout not on the watch must not be scheduled"
    assert removed == [], "nothing was on the watch to remove"
    assert resp["watch"] == {"repushed": [], "unscheduled": [], "failed": []}

    # And the title-only path must not newly schedule either.
    async with database.async_session() as s:
        out = await main._repush_after_changes(
            s, [{"workout_id": big_id, "field": "title", "value": "Renamed"}])
    assert pushed == [] and out["repushed"] == []


@pytest.mark.asyncio
async def test_a_garmin_failure_does_not_lose_the_applied_suggestion(monkeypatch):
    """The plan change is the point; the watch is a follower. A failed removal
    keeps the id (the session IS still on the watch) and is reported."""
    main, database = await _app(monkeypatch, "sr3")
    big_id = await _seed_cap_fixture(database, big_gid=7778, act_id=502)

    async def broken_push(w):
        raise RuntimeError("garmin 503")

    async def broken_remove(gid):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main, "_garmin_push", broken_push)
    monkeypatch.setattr(main.garmin, "remove_workout", broken_remove)

    resp = await _apply_cap(main, database)

    from models import PlannedWorkout as PW
    async with database.async_session() as s:
        w = await s.get(PW, big_id)
        assert w.target_distance_m == 5000, (
            "the suggestion must stay applied even though the watch call failed")
        assert w.garmin_workout_id == 7778, (
            "removal failed, so the session is still on the watch and the id "
            "must survive for a retry")
    assert [f["id"] for f in resp["watch"]["failed"]] == [big_id]
    assert resp["watch"]["unscheduled"] == []


@pytest.mark.asyncio
async def test_a_failed_repush_is_reported_not_swallowed(monkeypatch):
    main, database = await _app(monkeypatch, "sr6")
    big_id = await _seed_cap_fixture(database, big_gid=7780, act_id=504)

    async def broken_push(w):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main, "_garmin_push", broken_push)

    async with database.async_session() as s:
        out = await main._repush_after_changes(
            s, [{"workout_id": big_id, "field": "title", "value": "Renamed"}])

    assert [f["id"] for f in out["failed"]] == [big_id]
    assert out["repushed"] == []
