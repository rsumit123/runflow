"""Every data table is owned by a user, and the two natural keys are per-user."""
import tempfile
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import tenancy

pytestmark = pytest.mark.no_user

TENANT_TABLES = [
    "activities", "splits", "streams", "best_efforts", "chat_messages",
    "daily_wellness", "route_labels", "goals", "route_merges", "plans",
    "planned_workouts",
]


async def _fresh(monkeypatch, tag):
    import importlib, sys, config, database
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=f"{tag}.db"))
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401
    await database.init_db()
    return database


async def test_every_data_table_has_user_id(monkeypatch):
    database = await _fresh(monkeypatch, "s1")
    async with database.engine.connect() as conn:
        for table in TENANT_TABLES:
            cols = {r[1] for r in (await conn.exec_driver_sql(
                f"PRAGMA table_info({table})")).fetchall()}
            assert "user_id" in cols, table


async def test_auth_tables_exist(monkeypatch):
    database = await _fresh(monkeypatch, "s2")
    async with database.engine.connect() as conn:
        names = {r[0] for r in (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    assert {"users", "auth_sessions", "invites", "connector_tokens",
            "credentials"} <= names


async def test_two_users_can_hold_wellness_for_the_same_day(monkeypatch):
    database = await _fresh(monkeypatch, "s3")
    from models import DailyWellness
    for uid in (1, 2):
        with tenancy.user_context(uid):
            async with database.async_session() as s:
                s.add(DailyWellness(date="2026-10-13", resting_hr=50 + uid))
                await s.commit()
    with tenancy.user_context(2):
        async with database.async_session() as s:
            rows = (await s.execute(select(DailyWellness))).scalars().all()
    assert [(r.date, r.resting_hr) for r in rows] == [("2026-10-13", 52)]


async def test_route_key_is_unique_per_user_not_globally(monkeypatch):
    database = await _fresh(monkeypatch, "s4")
    from models import RouteLabel
    for uid in (1, 2):
        with tenancy.user_context(uid):
            async with database.async_session() as s:
                s.add(RouteLabel(route_key="loop-a", name=f"user {uid}"))
                await s.commit()
    with tenancy.user_context(1):
        async with database.async_session() as s:
            s.add(RouteLabel(route_key="loop-a", name="duplicate"))
            with pytest.raises(IntegrityError):
                await s.commit()


async def test_activities_are_stamped_and_scoped(monkeypatch):
    database = await _fresh(monkeypatch, "s5")
    from models import Activity
    with tenancy.user_context(1):
        async with database.async_session() as s:
            s.add(Activity(id=1, name="mine", start_date=datetime(2026, 10, 1)))
            await s.commit()
    with tenancy.user_context(2):
        async with database.async_session() as s:
            assert await s.get(Activity, 1) is None
