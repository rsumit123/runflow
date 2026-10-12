import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.mark.asyncio
async def test_create_get_abandon_plan(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Plan, PlannedWorkout
    import main
    importlib.reload(main)

    # Mock the LLM narrative — no network in tests.
    async def fake_narr(goal, weeks_overview):
        return {"overview": "Your comeback plan.", "weekly": [{"week": 1, "focus": "ease in"}]}
    monkeypatch.setattr(main.coach_llm, "generate_plan_narrative", fake_narr)

    now = datetime(2026, 7, 13, 12, 0, 0)
    async with database.async_session() as s:
        # Seed ~10 recent runs so projection + generator have data.
        for i in range(10):
            s.add(Activity(id=1000 + i, name="Run", distance=3000.0,
                           start_date=now - timedelta(days=2 * i),
                           average_speed=1000.0 / 360, average_heartrate=185.0, max_heartrate=205.0))
        await s.commit()

        req = main.PlanCreateRequest(weeks=8, target_time_sec=27 * 60 + 22)
        resp = await main.create_plan(req, s)
        assert resp["plan"]["status"] == "active"
        assert resp["plan"]["narrative"]["overview"] == "Your comeback plan."
        assert len(resp["workouts"]) > 0
        assert any(w["title"].startswith("Race day") for w in resp["workouts"])
        pid = resp["plan"]["id"]

        got = await main.get_active_plan(s)
        assert got["plan"]["id"] == pid

        await main.abandon_plan(pid, s)
        assert (await main.get_active_plan(s))["plan"] is None


@pytest.mark.asyncio
async def test_create_plan_rejects_bad_weeks(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import main
    importlib.reload(main)
    from fastapi import HTTPException

    async with database.async_session() as s:
        with pytest.raises(HTTPException):
            await main.create_plan(main.PlanCreateRequest(weeks=2, target_time_sec=1600), s)


@pytest.mark.asyncio
async def test_5k_plan_anchors_week_one_on_the_requested_start_date(monkeypatch):
    """start_date exists on the request but the 5K path ignored it.

    Building mid-week back-dates week 1 to its own Monday, which strands the
    early sessions in the past — or worse, schedules a run on the day the
    athlete just raced. The caller passes the upcoming Monday to avoid that,
    and the 5K path has to honour it the way the sprint path already does.
    """
    tmp = tempfile.mktemp(suffix="sd.db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, sys, config, database
    for _m in ("main", "mcp_server", "models"):
        sys.modules.pop(_m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401 — populates Base.metadata before init_db
    await database.init_db()
    from models import Activity
    import main
    importlib.reload(main)

    async def fake_narr(goal, weeks_overview):
        return {"overview": "ok", "weekly": []}
    monkeypatch.setattr(main.coach_llm, "generate_plan_narrative", fake_narr)

    now = datetime.utcnow()
    async with database.async_session() as s:
        for i in range(10):
            s.add(Activity(id=2000 + i, name="Run", distance=3000.0,
                           start_date=now - timedelta(days=2 * i),
                           average_speed=1000.0 / 435, average_heartrate=180.0,
                           max_heartrate=205.0))
        await s.commit()

        monday = (now + timedelta(days=(7 - now.weekday()) % 7 or 7)).date()
        resp = await main.create_plan(main.PlanCreateRequest(
            weeks=12, target_time_sec=1800, start_date=monday.isoformat()), s)

        first = min(w["date"] for w in resp["workouts"])
        assert str(first)[:10] == monday.isoformat(), (
            f"week 1 should start on the requested Monday {monday}, got {first}"
        )
        assert resp["plan"]["start_date"][:10] == monday.isoformat()
