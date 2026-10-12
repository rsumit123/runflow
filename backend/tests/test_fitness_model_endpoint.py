"""The numbers every plan is sized from, over HTTP.

Without this endpoint the only way to read the easy-pace method, the HR
ceiling or the acute:chronic load ratio is to run build_fitness_model inside
the container — which means the one number that says "do not ramp up yet" is
invisible to every client.
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


@pytest.mark.asyncio
async def test_fitness_model_endpoint_returns_the_documented_keys(monkeypatch):
    main, database = await _app(monkeypatch, "fm")
    from models import Activity

    now = datetime.utcnow()
    async with database.async_session() as s:
        for i in range(8):
            s.add(Activity(id=3000 + i, name="Run", distance=3500.0, moving_time=1600,
                           start_date=now - timedelta(days=3 * i),
                           average_speed=3500.0 / 1600, average_heartrate=178.0,
                           max_heartrate=200.0))
        await s.commit()

    async with database.async_session() as s:
        model = await main.get_fitness_model(s)

    for key in ("athlete_max_hr", "easy_hr_ceiling", "easy_pace_sec",
                "easy_pace_method", "weekly_volume_28d_km", "longest_run_28d_km",
                "acute_load_7d_km", "chronic_load_28d_km", "acwr", "total_runs"):
        assert key in model, f"{key} missing from the fitness model response"
    assert model["total_runs"] == 8


@pytest.mark.asyncio
async def test_fitness_model_endpoint_works_with_no_runs_at_all(monkeypatch):
    """A fresh install must not 500 — the model falls back to its defaults."""
    main, database = await _app(monkeypatch, "fm2")

    async with database.async_session() as s:
        model = await main.get_fitness_model(s)

    assert model["total_runs"] == 0
