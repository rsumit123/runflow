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
    assert len(out) < 25_200
