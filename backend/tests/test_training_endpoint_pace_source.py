"""The two endpoints that serve the fitness model must agree.

/api/training built its activity dicts inline and omitted normalized_pace_sec,
while /api/fitness-model and plan creation both use _plan_activity_dicts, which
includes it. fitness_model.py prefers the heat-normalised pace throughout, so
the same database produced an easy pace 40 s/km apart depending on which
endpoint you asked.
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
async def test_training_and_fitness_model_endpoints_report_the_same_pace(monkeypatch):
    main, database = await _app(monkeypatch, "tp")
    from models import Activity

    now = datetime.utcnow()
    async with database.async_session() as s:
        for i in range(8):
            # 2400 s raw over 6 km (400 s/km), heat-normalised to 360 s/km.
            s.add(Activity(id=4000 + i, name="Run", distance=6000.0, moving_time=2400,
                           start_date=now - timedelta(days=3 * i),
                           average_speed=6000.0 / 2400, average_heartrate=140.0,
                           max_heartrate=200.0, normalized_pace_sec=360.0))
        await s.commit()

    async with database.async_session() as s:
        report = await main.training(s)
    async with database.async_session() as s:
        model = await main.get_fitness_model(s)

    assert report["fitness_model"]["easy_pace_sec"] == model["easy_pace_sec"], (
        "the two endpoints disagree about the easy pace on the same database"
    )
    assert model["easy_pace_sec"] == 360, "the heat-normalised pace is the right one"
