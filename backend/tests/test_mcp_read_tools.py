"""The fat read tools, over a seeded database."""
import tempfile
from datetime import datetime, timedelta

import pytest

import mcp_reports as mr


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
    assert "status:" in out, "the planned section should carry its status"
    assert "compliance:" in out or "status: done" in out, (
        "grading is the point of the planned section — print what it found"
    )


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


@pytest.mark.asyncio
async def test_review_run_reports_a_failure_with_no_message(monkeypatch):
    """`if planned_error:` could not tell an empty message from no error.

    raise RuntimeError() gives an empty str(exc), which fell through to "No
    planned session matched this run." — the exact false statement the
    planned_error branch exists to prevent.
    """
    main, mcp_server, database = await _app(monkeypatch, "rrempty")
    from models import Activity
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    async with database.async_session() as s:
        s.add(Activity(id=702, name="Run", distance=5000.0, moving_time=2200,
                       elapsed_time=2202, start_date=now - timedelta(days=1),
                       average_speed=5000.0 / 2200, average_heartrate=180.0,
                       max_heartrate=190.0))
        await s.commit()

    async def broken(session, run_id):
        raise RuntimeError()
    monkeypatch.setattr(mcp_server, "_planned_for", broken)

    out = await mcp_server.review_run(run_id=702)

    assert "No planned session matched" not in out
    assert "Could not check this run against the plan" in out
    assert "RuntimeError" in out, "fall back to the class when there is no message"


def test_run_review_caps_a_runaway_plan_error():
    """A SQLAlchemy error can carry a whole statement and its parameters."""
    run = {"id": 1, "name": "R", "distance": 5000.0, "moving_time": 2200,
           "elapsed_time": 2202, "average_heartrate": 180.0,
           "max_heartrate": 190.0, "hr_zones": None, "start_latlng": None,
           "end_latlng": None, "start_date": None, "total_elevation_gain": 0.0}

    out = mr.run_review(run, {}, planned=None, planned_error="x" * 500)

    line = next(l for l in out.split("\n") if "Could not check" in l)
    assert len(line) < 300, f"the error line ran to {len(line)} characters"


def test_run_review_treats_an_empty_error_as_a_failure_not_an_absence():
    """Pins the `is not None` guard itself; the server-side class-name fallback
    would otherwise mask a regression to a truthiness check."""
    run = {"id": 1, "name": "R", "distance": 5000.0, "moving_time": 2200}

    out = mr.run_review(run, {}, planned=None, planned_error="")

    assert "Could not check this run against the plan" in out
    assert "No planned session matched" not in out


@pytest.mark.asyncio
async def test_get_fitness_model_reports_the_load_ratio_with_its_meaning(monkeypatch):
    """The raw ACWR number means nothing without the threshold beside it."""
    main, mcp_server, database = await _app(monkeypatch, "fmt")
    from models import Activity

    now = datetime.utcnow()
    async with database.async_session() as s:
        # One recent big week against a quiet month: a deliberately high ratio.
        s.add(Activity(id=1, name="Long", distance=6000.0, moving_time=2775,
                       start_date=now - timedelta(days=1),
                       average_speed=6000.0 / 2775, average_heartrate=181.0,
                       max_heartrate=194.0))
        for i in range(3):
            s.add(Activity(id=10 + i, name="Short", distance=2500.0, moving_time=1090,
                           start_date=now - timedelta(days=20 + 3 * i),
                           average_speed=2500.0 / 1090, average_heartrate=176.0,
                           max_heartrate=187.0))
        await s.commit()

    out = await mcp_server.get_fitness_model()

    assert "easy" in out.lower()
    assert "acwr" in out.lower() or "load ratio" in out.lower()
    assert "1.5" in out, "the risk threshold has to appear next to the number"


@pytest.mark.asyncio
async def test_get_fitness_model_flags_a_stale_easy_pace(monkeypatch):
    """measured_stale means the band is a fallback, not a measurement."""
    main, mcp_server, database = await _app(monkeypatch, "fmt2")
    from models import Activity

    now = datetime.utcnow()
    async with database.async_session() as s:
        for i in range(5):
            s.add(Activity(id=20 + i, name="Hard", distance=3000.0, moving_time=1300,
                           start_date=now - timedelta(days=4 * i),
                           average_speed=3000.0 / 1300, average_heartrate=186.0,
                           max_heartrate=205.0))
        await s.commit()

    out = await mcp_server.get_fitness_model()

    assert "ESTIMATE, not measured" in out
    assert "7:30" in out and "floor clamp" in out
    assert "/km/km" not in out


@pytest.mark.asyncio
async def test_get_fitness_model_sparse_data_does_not_crash(monkeypatch):
    """No runs at all: every optional field is None; ACWR must say so."""
    main, mcp_server, database = await _app(monkeypatch, "fmt3")
    out = await mcp_server.get_fitness_model()
    assert "not enough history" in out
    assert "—" in out


@pytest.mark.asyncio
async def test_get_fitness_model_thin_baseline_is_not_called_a_spike(monkeypatch):
    """A rebuild block has a tiny chronic load; a big ratio there is not a spike."""
    main, mcp_server, database = await _app(monkeypatch, "fmt4")
    from models import Activity
    now = datetime.utcnow()
    async with database.async_session() as s:
        s.add(Activity(id=1, name="Run", distance=3000.0, moving_time=1300,
                       start_date=now - timedelta(days=1), average_speed=3000.0 / 1300,
                       average_heartrate=150.0, max_heartrate=190.0))
        await s.commit()
    out = await mcp_server.get_fitness_model()
    assert "thin baseline" in out
    assert "do not add" not in out


@pytest.mark.asyncio
async def test_get_fitness_model_real_spike_says_hold_volume(monkeypatch):
    """With a real baseline (>=5 km/week) a ratio over 1.5 is a genuine spike."""
    main, mcp_server, database = await _app(monkeypatch, "fmt5")
    from models import Activity
    now = datetime.utcnow()
    async with database.async_session() as s:
        s.add(Activity(id=1, name="Big", distance=20000.0, moving_time=7000,
                       start_date=now - timedelta(days=1), average_speed=20000.0 / 7000,
                       average_heartrate=150.0, max_heartrate=190.0))
        for i in range(3):
            s.add(Activity(id=10 + i, name="Base", distance=5000.0, moving_time=1800,
                           start_date=now - timedelta(days=10 + 5 * i),
                           average_speed=5000.0 / 1800, average_heartrate=150.0,
                           max_heartrate=190.0))
        await s.commit()
    out = await mcp_server.get_fitness_model()
    assert "ABOVE the 1.5 risk threshold" in out and "do not add" in out
    assert "thin baseline" not in out
