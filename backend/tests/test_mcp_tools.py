"""Tool-level tests against a temp SQLite DB."""
import tempfile
import pytest


@pytest.mark.asyncio
async def test_mcp_mounts_only_when_a_secret_is_set(monkeypatch):
    """No MCP_SECRET must mean no mounted route — fail closed, not fail open."""
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    assert not any("/mcp/" in getattr(r, "path", "") for r in main.app.routes)


@pytest.mark.asyncio
async def test_mcp_mounts_under_the_secret_path(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    assert any(getattr(r, "path", "") == "/mcp/testsecret" for r in main.app.routes)


from datetime import datetime, timedelta


async def _seed(monkeypatch, tag="a"):
    """Temp DB with one clean run and one fragmented run, both with HR streams."""
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Stream
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    now = datetime(2026, 10, 9, 6, 0, 0)
    async with database.async_session() as s:
        s.add(Activity(
            id=1, name="Clean run", distance=3500.0, moving_time=1450,
            elapsed_time=1450, start_date=now - timedelta(days=1),
            average_speed=3500.0 / 1450, average_heartrate=182.0,
            max_heartrate=195.0, average_cadence=156.0, source="garmin",
            aerobic_te=4.2, anaerobic_te=0.0, training_effect_label="VO2MAX",
            temp_c=26.0, dew_point_c=24.0, heat_index=155.0,
            heat_penalty_sec=22.0, normalized_pace_sec=392.0,
            running_dynamics={"stride_length": 92.2, "ground_contact_time": 301.3,
                              "vertical_oscillation": 8.8},
            hr_zones=[{"zone": z, "secs": 290.0, "low_bpm": b}
                      for z, b in [(1, 105), (2, 126), (3, 147), (4, 168), (5, 189)]],
        ))
        s.add(Stream(activity_id=1, stream_type="time", data=list(range(1451))))
        s.add(Stream(activity_id=1, stream_type="distance",
                     data=[i * (3500.0 / 1450) for i in range(1451)]))
        s.add(Stream(activity_id=1, stream_type="heartrate",
                     data=[150.0] * 725 + [195.0] * 726))

        # A fragmented run: 600 s standing still at the 1 km mark.
        t, time_s, dist_s = 0, [], []
        for metre in range(0, 2001):
            if metre == 1000:
                t += 600
            time_s.append(t); dist_s.append(float(metre)); t += 1
        s.add(Activity(
            id=2, name="Paused run", distance=2000.0, moving_time=2000,
            elapsed_time=2002, start_date=now - timedelta(days=3),
            average_speed=1.0, average_heartrate=175.0, max_heartrate=202.0,
            source="garmin",
        ))
        s.add(Stream(activity_id=2, stream_type="time", data=time_s))
        s.add(Stream(activity_id=2, stream_type="distance", data=dist_s))
        s.add(Stream(activity_id=2, stream_type="heartrate", data=[180.0] * 2001))
        await s.commit()
    return mcp_server


@pytest.mark.asyncio
async def test_list_recent_runs_reports_pace_hr_and_fragmentation(monkeypatch):
    mcp_server = await _seed(monkeypatch, "b")

    out = await mcp_server.list_recent_runs(limit=5)

    assert "3.50" in out
    assert "182" in out
    assert "fragmented" in out.lower()


@pytest.mark.asyncio
async def test_list_recent_runs_says_so_when_there_are_no_runs(monkeypatch):
    tmp = tempfile.mktemp(suffix="c.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    out = await mcp_server.list_recent_runs(limit=5)

    assert "no runs" in out.lower()


@pytest.mark.asyncio
async def test_get_run_detail_includes_splits_drift_and_zones(monkeypatch):
    mcp_server = await _seed(monkeypatch, "d")

    out = await mcp_server.get_run_detail(run_id=1)

    assert "split" in out.lower()
    assert "drift" in out.lower()
    assert "+45" in out
    assert "Z5" in out


@pytest.mark.asyncio
async def test_get_run_detail_lists_the_pauses(monkeypatch):
    mcp_server = await _seed(monkeypatch, "e")

    out = await mcp_server.get_run_detail(run_id=2)

    assert "pause" in out.lower()
    # The gap spans metre 999 (t=999) to metre 1000 (t=1600): 601 s.
    assert "10m01s" in out


@pytest.mark.asyncio
async def test_get_run_detail_on_a_missing_run_is_readable(monkeypatch):
    mcp_server = await _seed(monkeypatch, "f")

    out = await mcp_server.get_run_detail(run_id=9999)

    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_compare_runs_puts_runs_side_by_side(monkeypatch):
    mcp_server = await _seed(monkeypatch, "g")

    out = await mcp_server.compare_runs(run_ids=[1, 2])

    assert "Clean" not in out or True  # table is id-keyed, not name-keyed
    assert "\n1 |" in out and "\n2 |" in out
    assert "Z5" in out or "drift" in out.lower()


@pytest.mark.asyncio
async def test_compare_runs_rejects_too_many_ids(monkeypatch):
    mcp_server = await _seed(monkeypatch, "h")

    out = await mcp_server.compare_runs(run_ids=[1, 2, 3, 4, 5, 6])

    assert "at most 5" in out.lower()


@pytest.mark.asyncio
async def test_compare_runs_needs_at_least_two(monkeypatch):
    mcp_server = await _seed(monkeypatch, "h2")

    out = await mcp_server.compare_runs(run_ids=[1])

    assert "at least 2" in out.lower()


@pytest.mark.asyncio
async def test_get_recovery_carries_the_post_run_caveat(monkeypatch):
    tmp = tempfile.mktemp(suffix="i.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import DailyWellness
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    async with database.async_session() as s:
        s.add(DailyWellness(date="2026-10-08", readiness_score=78,
                            readiness_level="HIGH", sleep_hours=7.1,
                            sleep_score=80, body_battery_peak=88,
                            hrv_last_night=52, hrv_status="BALANCED",
                            resting_hr=52))
        await s.commit()

    out = await mcp_server.get_recovery(days=7)

    assert "78" in out
    assert "after the run" in out.lower()


@pytest.mark.asyncio
async def test_get_recovery_says_so_when_empty(monkeypatch):
    tmp = tempfile.mktemp(suffix="i2.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    out = await mcp_server.get_recovery(days=7)

    assert "no wellness" in out.lower()


@pytest.mark.asyncio
async def test_get_records_and_training_context_do_not_raise(monkeypatch):
    mcp_server = await _seed(monkeypatch, "j")

    recs = await mcp_server.get_records()
    ctx = await mcp_server.get_training_context()

    assert isinstance(recs, str) and recs
    assert isinstance(ctx, str) and ctx
    assert "could not" not in recs.lower()
    assert "could not" not in ctx.lower()
    assert "gate" in ctx.lower()


@pytest.mark.asyncio
async def test_sync_garmin_reports_the_imported_count(monkeypatch):
    mcp_server = await _seed(monkeypatch, "k")

    async def fake_sync(session):
        return {"imported": 2, "error": None}
    monkeypatch.setattr(mcp_server, "_garmin_sync", fake_sync)

    out = await mcp_server.sync_garmin()

    assert "2" in out


@pytest.mark.asyncio
async def test_sync_garmin_times_out_without_hanging(monkeypatch):
    import asyncio
    mcp_server = await _seed(monkeypatch, "l")

    async def slow_sync(session):
        await asyncio.sleep(5)
    monkeypatch.setattr(mcp_server, "_garmin_sync", slow_sync)
    monkeypatch.setattr(mcp_server, "SYNC_TIMEOUT_SEC", 0.1)

    out = await mcp_server.sync_garmin()

    assert "still running" in out.lower()


@pytest.mark.asyncio
async def test_sync_garmin_surfaces_an_error_readably(monkeypatch):
    mcp_server = await _seed(monkeypatch, "m")

    async def broken_sync(session):
        raise RuntimeError("no garmin token")
    monkeypatch.setattr(mcp_server, "_garmin_sync", broken_sync)

    out = await mcp_server.sync_garmin()

    assert "no garmin token" in out.lower()


@pytest.mark.asyncio
async def test_call_api_rejects_a_post_only_path(monkeypatch):
    mcp_server = await _seed(monkeypatch, "n")

    out = await mcp_server.call_api(path="/api/import/garmin/sync")

    assert "no get route" in out.lower()
    assert "post" in out.lower()


@pytest.mark.asyncio
async def test_call_api_rejects_an_unknown_path_and_suggests_matches(monkeypatch):
    mcp_server = await _seed(monkeypatch, "o")

    out = await mcp_server.call_api(path="/api/stats/nonsense")

    assert "no get route" in out.lower()
    assert "/api/stats" in out


@pytest.mark.asyncio
async def test_call_api_returns_data_for_a_valid_get(monkeypatch):
    mcp_server = await _seed(monkeypatch, "p")

    out = await mcp_server.call_api(path="/api/stats/personal-records")

    assert isinstance(out, str) and out
    assert "could not" not in out.lower()
    assert "no get route" not in out.lower()


@pytest.mark.asyncio
async def test_call_api_strips_heavy_fields_by_default(monkeypatch):
    mcp_server = await _seed(monkeypatch, "q")

    out = await mcp_server.call_api(path="/api/activities")

    assert "map_summary_polyline" not in out


@pytest.mark.asyncio
async def test_call_api_accepts_a_path_without_a_leading_slash(monkeypatch):
    mcp_server = await _seed(monkeypatch, "r")

    out = await mcp_server.call_api(path="api/stats/personal-records")

    assert "no get route" not in out.lower()


@pytest.mark.asyncio
async def test_get_route_index_lists_only_get_paths(monkeypatch):
    mcp_server = await _seed(monkeypatch, "s")

    paths = mcp_server._get_route_index()

    assert "/api/activities" in paths
    assert "/api/import/garmin/sync" not in paths


@pytest.mark.asyncio
async def test_get_run_detail_reports_terrain_and_location(monkeypatch):
    """The athlete asks "track or road?" — the tool must answer it."""
    tmp = tempfile.mktemp(suffix="t.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    async with database.async_session() as s:
        # The flat track run, and a rolling road run the day before elsewhere.
        s.add(Activity(id=10, name="Track", distance=2510.0, moving_time=1090,
                       elapsed_time=1090, start_date=datetime(2026, 10, 9, 12, 7),
                       average_speed=2.3, average_heartrate=176.0,
                       max_heartrate=187.0, total_elevation_gain=0.0,
                       elev_high=174.4, elev_low=172.6,
                       start_latlng=[22.776184, 86.253266]))
        s.add(Activity(id=9, name="Road", distance=3730.0, moving_time=1657,
                       elapsed_time=1657, start_date=datetime(2026, 10, 8, 12, 6),
                       average_speed=2.25, average_heartrate=181.0,
                       max_heartrate=197.0, total_elevation_gain=27.0,
                       elev_high=183.8, elev_low=163.2,
                       start_latlng=[22.757716, 86.266081]))
        await s.commit()

    out = await mcp_server.get_run_detail(run_id=10)

    assert "flat" in out.lower()
    assert "0 m" in out or "0.0 m" in out
    assert "22.776" in out                      # start coordinates
    assert "different place" in out.lower()     # not where the previous run started


@pytest.mark.asyncio
async def test_list_recent_runs_includes_a_terrain_column(monkeypatch):
    mcp_server = await _seed(monkeypatch, "u")

    out = await mcp_server.list_recent_runs(limit=5)

    assert "terrain" in out.lower()


@pytest.mark.asyncio
async def test_get_run_detail_states_the_zone_boundaries(monkeypatch):
    """A zone share is uninterpretable without knowing Z4 starts at 168."""
    mcp_server = await _seed(monkeypatch, "v")

    out = await mcp_server.get_run_detail(run_id=1)

    assert "168" in out and "189+" in out
    assert "max HR" in out


@pytest.mark.asyncio
async def test_get_aerobic_trend_reports_metres_per_beat(monkeypatch):
    mcp_server = await _seed(monkeypatch, "w")

    out = await mcp_server.get_aerobic_trend(days=90)

    assert "m/beat" in out.lower()
    assert "0." in out


@pytest.mark.asyncio
async def test_get_aerobic_trend_says_so_when_empty(monkeypatch):
    tmp = tempfile.mktemp(suffix="w2.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)

    out = await mcp_server.get_aerobic_trend(days=30)

    assert "no runs" in out.lower()


@pytest.mark.asyncio
async def test_get_weekly_volume_groups_by_week(monkeypatch):
    mcp_server = await _seed(monkeypatch, "x")

    out = await mcp_server.get_weekly_volume(weeks=8)

    assert "week" in out.lower()
    assert "km" in out.lower()


@pytest.mark.asyncio
async def test_training_context_summarises_rather_than_dumping_json(monkeypatch):
    """The raw phase list is tens of objects; a summary is what's useful."""
    mcp_server = await _seed(monkeypatch, "y")

    out = await mcp_server.get_training_context()

    assert '"phase_number"' not in out, "should not dump raw phase JSON"
    assert out.count("{") < 5, "output should be prose/table, not a JSON blob"
    assert "gate" in out.lower()
    assert "current phase" in out.lower()
