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


@pytest.mark.asyncio
async def test_get_fitness_model_says_when_a_measured_pace_was_clamped(monkeypatch):
    """A measured 8:40 shown as 7:30 hides that the athlete is slower than the number."""
    main, mcp_server, database = await _app(monkeypatch, "fmt6")
    from models import Activity
    now = datetime.utcnow()
    async with database.async_session() as s:
        for i in range(6):
            s.add(Activity(id=30 + i, name="Easy", distance=5000.0, moving_time=2600,
                           start_date=now - timedelta(days=3 * (i + 1)),
                           average_speed=5000.0 / 2600, average_heartrate=120.0,
                           max_heartrate=200.0))
        await s.commit()
    out = await mcp_server.get_fitness_model()
    assert "measured at 8:40/km and clamped to the 7:30/km floor" in out
    assert "ESTIMATE" not in out


@pytest.mark.asyncio
async def test_get_training_block_aggregates_by_month_over_a_long_range(monkeypatch):
    """The comparison that answers 'have I declined' spans years.

    Fetching the runs raw would exceed the response cap; aggregating
    server-side is the point of the tool.
    """
    main, mcp_server, database = await _app(monkeypatch, "tb")
    from models import Activity

    async with database.async_session() as s:
        # 29 daily runs in April 2021 at 6:51/km, then 4 in October 2026 at 7:15.
        for d in range(1, 30):
            s.add(Activity(id=1000 + d, name="Run", distance=5050.0, moving_time=2077,
                           start_date=datetime(2021, 4, d, 7, 0),
                           average_speed=5050.0 / 2077))
        for d in range(6, 10):
            s.add(Activity(id=2000 + d, name="Run", distance=3000.0, moving_time=1305,
                           start_date=datetime(2026, 10, d, 7, 0),
                           average_speed=3000.0 / 1305))
        await s.commit()

    out = await mcp_server.get_training_block(start="2021-01-01", end="2026-12-31",
                                             by="month")

    assert "2021-04" in out
    assert "2026-10" in out
    assert "29" in out, "days run in April 2021"
    assert len(out) <= 25_200

    header = [c.strip() for c in out.splitlines()[0].split(" | ")]
    rate = lambda p: float(_row(out, p)[header.index("km/wk")])
    # A complete month must be unaffected by the partial-month fix:
    # 146.45 km / (30/7) weeks = 34.2.
    assert 33.0 <= rate("2021-04") <= 35.0
    # October 2026 is in progress (today is inside it): 12 km over the days
    # elapsed, not over a fixed 4.35 weeks.
    assert rate("2026-10") > 4.0


@pytest.mark.asyncio
async def test_get_training_block_supports_weekly_granularity(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "tb2")
    from models import Activity

    async with database.async_session() as s:
        for d in (6, 7, 8, 9):
            s.add(Activity(id=3000 + d, name="Run", distance=3000.0, moving_time=1305,
                           start_date=datetime(2026, 10, d, 7, 0),
                           average_speed=3000.0 / 1305))
        await s.commit()

    out = await mcp_server.get_training_block(start="2026-10-01", end="2026-10-31",
                                             by="week")

    assert "W41" in out


@pytest.mark.asyncio
async def test_get_training_block_rejects_a_bad_date(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "tb3")

    out = await mcp_server.get_training_block(start="last April", end="2026-12-31")

    assert "iso date" in out.lower() or "yyyy-mm-dd" in out.lower()


@pytest.mark.asyncio
async def test_get_training_block_says_so_when_the_range_is_empty(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "tb4")

    out = await mcp_server.get_training_block(start="2019-01-01", end="2019-12-31")

    assert "no runs" in out.lower()


def _row(out, period):
    """The cells of the table row starting with `period`, split on ' | '."""
    rows = [ln for ln in out.splitlines() if ln.startswith(period + " |")]
    assert len(rows) == 1, f"expected exactly one {period} row in:\n{out}"
    return [c.strip() for c in rows[0].split(" | ")]


@pytest.mark.asyncio
async def test_get_training_block_monthly_cells_are_in_the_right_columns(monkeypatch):
    """Pin each number to its column — a bare `"29" in out` matches any 29."""
    main, mcp_server, database = await _app(monkeypatch, "tb5")
    from models import Activity

    async with database.async_session() as s:
        # 20 runs across 20 days, plus a second run on day 1 and a slow one on day 2.
        for d in range(1, 21):
            s.add(Activity(id=100 + d, name="Run", distance=5000.0, moving_time=1800,
                           start_date=datetime(2021, 4, d, 7, 0)))
        s.add(Activity(id=200, name="Run", distance=5000.0, moving_time=1800,
                       start_date=datetime(2021, 4, 1, 18, 0)))
        # One long slow run drags the mean (6:16) but not the median (6:00).
        s.add(Activity(id=201, name="Run", distance=5000.0, moving_time=3600,
                       start_date=datetime(2021, 4, 2, 18, 0)))
        await s.commit()

    out = await mcp_server.get_training_block(start="2021-04-01", end="2021-04-30")
    header = [c.strip() for c in out.splitlines()[0].split(" | ")]
    cells = _row(out, "2021-04")

    assert len(cells) == len(header)
    by_col = dict(zip(header, cells))
    assert by_col["runs"] == "22"
    assert by_col["days run"] == "20"
    assert by_col["km"] == "110.0"
    assert by_col["median pace"] == "6:00/km"
    assert by_col["mean pace"] == "6:16/km"
    assert by_col["km/wk"] == "25.7"  # 110 km over April's 30 days


@pytest.mark.asyncio
async def test_get_training_block_no_hr_data_reads_as_absent_not_zero(monkeypatch):
    """The 2021 block has no HR at all; a measured 0.0% would be a lie."""
    main, mcp_server, database = await _app(monkeypatch, "tb6")
    from models import Activity

    async with database.async_session() as s:
        s.add(Activity(id=1, name="Run", distance=5000.0, moving_time=1800,
                       start_date=datetime(2021, 4, 3, 7, 0), hr_zones=None))
        s.add(Activity(id=2, name="Run", distance=3000.0, moving_time=1300,
                       start_date=datetime(2026, 10, 6, 7, 0),
                       hr_zones=[{"zone": 5, "secs": 130.0}, {"zone": 2, "secs": 1170.0}]))
        await s.commit()

    for by, old, new in (("month", "2021-04", "2026-10"), ("week", "2021-W13", "2026-W41")):
        out = await mcp_server.get_training_block(start="2021-01-01", end="2026-12-31", by=by)
        header = [c.strip() for c in out.splitlines()[0].split(" | ")]
        z5 = header.index("Z5%")
        assert _row(out, old)[z5] == "—", out
        assert _row(out, new)[z5] == "10", out
        assert "0.0%" not in out and "—%" not in out


@pytest.mark.asyncio
async def test_get_training_block_weekly_row_has_no_monthly_columns(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "tb7")
    from models import Activity

    async with database.async_session() as s:
        for d in (6, 7, 8):
            s.add(Activity(id=300 + d, name="Run", distance=3000.0, moving_time=1305,
                           start_date=datetime(2026, 10, d, 7, 0)))
        await s.commit()

    out = await mcp_server.get_training_block(start="2026-10-01", end="2026-10-31", by="week")
    header = [c.strip() for c in out.splitlines()[0].split(" | ")]
    by_col = dict(zip(header, _row(out, "2026-W41")))

    assert "median pace" not in header and "days run" not in header
    assert "km/wk" not in header, "weekly km already is the rate"
    assert by_col["runs"] == "3"
    assert by_col["km"] == "9.0"
    assert by_col["mean pace"] == "7:15/km"


@pytest.mark.asyncio
async def test_get_training_block_reversed_range_and_bad_granularity(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "tb8")
    from models import Activity

    async with database.async_session() as s:
        s.add(Activity(id=1, name="Run", distance=3000.0, moving_time=1305,
                       start_date=datetime(2026, 10, 6, 7, 0)))
        await s.commit()

    rev = await mcp_server.get_training_block(start="2026-12-31", end="2026-01-01")
    assert "before" in rev.lower() and "no runs" not in rev.lower()

    bad = await mcp_server.get_training_block(start="2026-01-01", end="2026-12-31", by="day")
    assert "month" in bad and "week" in bad and "2026-" not in bad.split("—")[0]


@pytest.mark.asyncio
async def test_get_training_block_is_bounded_for_a_huge_weekly_range(monkeypatch):
    """Output size must be bounded by construction, not left to the cap."""
    main, mcp_server, database = await _app(monkeypatch, "tb9")
    from models import Activity

    async with database.async_session() as s:
        d0 = datetime(2010, 1, 4, 7, 0)
        for i in range(1000):
            s.add(Activity(id=i + 1, name="Run", distance=5000.0, moving_time=1800,
                           start_date=d0 + timedelta(weeks=i)))
        await s.commit()

    out = await mcp_server.get_training_block(start="2000-01-01", end="2030-12-31", by="week")

    assert "truncated at" not in out
    assert len(out) <= 25_000
    assert "narrower" in out.lower() or "by=\"month\"" in out


@pytest.mark.asyncio
async def test_training_block_rate_accounts_for_a_partial_month(monkeypatch):
    """km / 4.35 understated an in-progress month by 2.5x.

    That column is the "now" half of a historical comparison, so the error
    pushed the current block to look lighter than it really was.
    """
    main, mcp_server, database = await _app(monkeypatch, "tbrate")
    from models import Activity

    # Four 3 km runs in the first seven days of a month: 12 km in one week.
    async with database.async_session() as s:
        for i in range(4):
            s.add(Activity(id=900 + i, name="Run", distance=3000.0,
                           moving_time=1300,
                           start_date=datetime(2026, 10, 2 + i),
                           average_speed=3000.0 / 1300,
                           average_heartrate=170.0, max_heartrate=200.0))
        await s.commit()

    out = await mcp_server.get_training_block(
        start="2026-10-01", end="2026-10-07", by="month")

    header = next(l for l in out.split("\n") if "period" in l)
    cols = [c.strip() for c in header.split("|")]
    row = next(l for l in out.split("\n") if l.startswith("2026-10"))
    cells = [c.strip() for c in row.split("|")]
    rate = float(cells[cols.index("km/wk")])
    print("ACTUAL km/wk:", rate)

    assert 11.0 <= rate <= 13.0, (
        f"12 km over a seven-day range is about 12 km/wk, got {rate} — "
        "dividing by a fixed 4.35 weeks understates a partial month"
    )


def test_weeks_covered_clips_to_range_and_today_and_floors_at_one_week(monkeypatch):
    import sys
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import mcp_server
    from datetime import date
    wc = mcp_server._weeks_covered
    far = date(2030, 1, 1)
    # complete month: its own length
    assert wc(2021, 4, date(2021, 1, 1), date(2021, 12, 31), far) == pytest.approx(30 / 7)
    # in-progress month: clipped to today
    assert wc(2026, 10, date(2021, 1, 1), date(2026, 12, 31), date(2026, 10, 12)) == pytest.approx(12 / 7)
    # range clips both ends
    assert wc(2026, 10, date(2026, 10, 8), date(2026, 10, 21), far) == pytest.approx(2.0)
    # a month wholly after today (last < first): finite, floored at one week
    assert wc(2026, 11, date(2026, 1, 1), date(2026, 12, 31), date(2026, 10, 12)) == 1.0
    # a one-day span does not inflate the rate sevenfold
    assert wc(2026, 10, date(2026, 10, 1), date(2026, 12, 31), date(2026, 10, 1)) == 1.0
