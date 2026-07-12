import os, tempfile, asyncio
import pytest

async def _columns(engine, table):
    from sqlalchemy import text
    async with engine.begin() as conn:
        rows = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return {r[1] for r in rows.fetchall()}

@pytest.mark.asyncio
async def test_migration_adds_columns(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    acols = await _columns(database.engine, "activities")
    scols = await _columns(database.engine, "splits")
    assert {"source", "average_heartrate", "max_heartrate",
            "average_cadence", "hr_zones", "running_dynamics"} <= acols
    assert "average_cadence" in scols
