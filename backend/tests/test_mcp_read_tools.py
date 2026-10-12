"""The fat read tools, over a seeded database."""
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


async def _seed_run(database, run_id=1, metres=3000, pace=435):
    from models import Activity, Stream
    time, dist, hr = [], [], []
    for m in range(0, metres + 1):
        time.append(round(m * pace / 1000.0))
        dist.append(float(m))
        hr.append(180.0)
    async with database.async_session() as s:
        s.add(Activity(id=run_id, name="Track run", distance=float(metres),
                       moving_time=time[-1], elapsed_time=time[-1] + 2,
                       start_date=datetime(2026, 10, 12, 1, 45),
                       average_speed=metres / time[-1], average_heartrate=180.0,
                       max_heartrate=194.0, total_elevation_gain=0.0,
                       elev_high=174.4, elev_low=172.6))
        s.add(Stream(activity_id=run_id, stream_type="time", data=time))
        s.add(Stream(activity_id=run_id, stream_type="distance", data=dist))
        s.add(Stream(activity_id=run_id, stream_type="heartrate", data=hr))
        await s.commit()


@pytest.mark.asyncio
async def test_review_run_returns_splits_and_an_hr_profile(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "rr")
    await _seed_run(database)

    out = await mcp_server.review_run(run_id=1)

    assert "3.0 km" in out
    assert "splits" in out.lower()
    assert "hr profile" in out.lower()
    assert "continuous" in out.lower()


@pytest.mark.asyncio
async def test_review_run_on_a_missing_run_is_readable(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "rr2")

    out = await mcp_server.review_run(run_id=9999)

    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_review_run_clamps_an_absurd_window(monkeypatch):
    """A 1 s window on a 40 minute run would be 2400 rows of noise."""
    main, mcp_server, database = await _app(monkeypatch, "rr3")
    await _seed_run(database)

    out = await mcp_server.review_run(run_id=1, window_sec=1)

    assert "hr profile" in out.lower()
    assert "10 s windows" in out, (
        "window_sec=1 must clamp to the 10 s floor; unclamped the composer "
        "widens it to 8 s on its own, which is why the old length assertion "
        "passed with the clamp removed"
    )


@pytest.mark.asyncio
async def test_review_run_shows_the_planned_session_it_matched(monkeypatch):
    """The match path was never exercised, so a wrong key would go unnoticed.

    match_and_grade puts the matched run under `actual`, keyed activity_id —
    not under `matched_activity.id`, which an earlier draft of this plan
    claimed. Nothing proved which one was right until this test.
    """
    main, mcp_server, database = await _app(monkeypatch, "rrplan")
    from models import Activity, Plan, PlannedWorkout
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1800,
                    start_date=now - timedelta(days=14),
                    goal_date=now + timedelta(days=70),
                    weeks=12, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=1),
                             week_number=2, day_type="long",
                             target_distance_m=5000, hr_ceiling=165,
                             pace_low_sec=435, pace_high_sec=450,
                             title="Long run", description="x"))
        s.add(Activity(id=700, name="Track run", distance=5000.0,
                       moving_time=2200, elapsed_time=2202,
                       start_date=now - timedelta(days=1),
                       average_speed=5000.0 / 2200, average_heartrate=180.0,
                       max_heartrate=190.0))
        await s.commit()

    out = await mcp_server.review_run(run_id=700)

    assert "No planned session matched" not in out, (
        "the run should have matched the planned long run"
    )
    assert "Long run" in out
    assert "Planned:" in out


@pytest.mark.asyncio
async def test_review_run_says_so_when_it_cannot_check_the_plan(monkeypatch):
    """A failed check used to render as 'No planned session matched this run.'

    That tells the athlete nothing was planned on a day something was, which is
    a wrong answer stated confidently rather than a missing one.
    """
    main, mcp_server, database = await _app(monkeypatch, "rrerr")
    from models import Activity
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    async with database.async_session() as s:
        s.add(Activity(id=701, name="Run", distance=5000.0, moving_time=2200,
                       elapsed_time=2202, start_date=now - timedelta(days=1),
                       average_speed=5000.0 / 2200, average_heartrate=180.0,
                       max_heartrate=190.0))
        await s.commit()

    async def broken(session, run_id):
        raise RuntimeError("plan table is wedged")
    monkeypatch.setattr(mcp_server, "_planned_for", broken)

    out = await mcp_server.review_run(run_id=701)

    assert "Could not check this run against the plan" in out
    assert "plan table is wedged" in out
    assert "No planned session matched" not in out, (
        "a failed check must not be reported as an absence of planned work"
    )
    # The run's own data must still be there — a plan problem cannot deny it.
    assert "5.0 km" in out
