import tempfile
from datetime import datetime, timedelta
import pytest


def _interval_config():
    segs = [{"type": "warmup", "distance_m": 480, "duration_s": 180, "pace_sec_per_km": 375.2}]
    durs = [33, 35, 33, 39, 42, 46]
    for i, d in enumerate(durs, 1):
        segs.append({"type": "rep", "rep_number": i, "distance_m": 150, "duration_s": d,
                     "pace_sec_per_km": round(d / 0.15, 1)})
        segs.append({"type": "rest", "rest_number": i, "distance_m": 220, "duration_s": 182})
    return {"reps": 6, "distance": 150, "result": {
        "is_interval": True, "segments": segs,
        "summary": {"total_reps": 6, "fastest_rep_pace": 217.8, "slowest_rep_pace": 305.0,
                    "fastest_rep": 3, "slowest_rep": 6}}}


async def _seed_sprint_data(database, now):
    from models import Activity, BestEffort
    async with database.async_session() as s:
        # An interval-tagged session ~40 days ago with rep structure.
        act = Activity(id=5000, name="Morning Run", distance=2629.0,
                       start_date=now - timedelta(days=40), average_speed=1000.0 / 360,
                       is_interval=True, interval_config=_interval_config())
        s.add(act)
        # Best 100m efforts: fastest 19.0s on that session.
        s.add(BestEffort(activity_id=5000, distance_target=100, time_seconds=19.0, pace_sec_per_km=190.0))
        s.add(BestEffort(activity_id=5000, distance_target=200, time_seconds=70.0, pace_sec_per_km=350.0))
        await s.commit()


@pytest.mark.asyncio
async def test_sprint_baseline_and_projections(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import main
    importlib.reload(main)

    now = datetime(2026, 7, 13, 12, 0, 0)
    await _seed_sprint_data(database, now)

    async with database.async_session() as s:
        profile = await main.sprint_baseline_endpoint(s)
        assert profile["best_100m_sec"] == 19.0
        assert profile["diagnosis"] == "speed_endurance"
        assert profile["fade_pct"] and profile["fade_pct"] > 20

        proj = await main.sprint_projections_endpoint(s)
        assert proj["current_100m_sec"] == 19.0
        weeks = {h["weeks"]: h["target_100m_sec"] for h in proj["horizons"]}
        assert weeks[8] < 19.0 and weeks[12] < weeks[8]  # faster targets at longer horizons


@pytest.mark.asyncio
async def test_create_sprint_plan_and_detail(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import PlannedWorkout
    import main
    importlib.reload(main)

    async def fake_sprint_narr(goal, profile, weeks_overview):
        return {"overview": "Attack the fade.", "weekly": [{"week": 1, "focus": "baseline test"}]}
    monkeypatch.setattr(main.coach_llm, "generate_sprint_narrative", fake_sprint_narr)

    now = datetime(2026, 7, 13, 12, 0, 0)
    await _seed_sprint_data(database, now)

    async with database.async_session() as s:
        req = main.PlanCreateRequest(weeks=8, goal_type="sprint_100m")
        resp = await main.create_plan(req, s)
        assert resp["plan"]["goal_type"] == "sprint_100m"
        assert resp["plan"]["sprint_target_sec"] and resp["plan"]["sprint_target_sec"] < 19.0
        assert resp["plan"]["narrative"]["overview"] == "Attack the fade."
        assert resp["plan"]["profile"]["diagnosis"] == "speed_endurance"
        assert "progress" in resp
        # Sprint workouts carry a structure; final one is a 100m test.
        assert all(w.get("structure") for w in resp["workouts"] if w["day_type"] != "rest")
        finals = [w for w in resp["workouts"] if w["day_type"] == "test"]
        assert finals and any(w["day_type"] == "test" for w in resp["workouts"])

        # Detail for the first (baseline test) sprint workout renders structure.
        first = sorted(resp["workouts"], key=lambda w: w["date"])[0]
        detail = await main.plan_workout_detail(first["id"], s)
        assert detail["workout"]["structure"] is not None
        assert detail["plan"]["goal_type"] == "sprint_100m"
        assert detail["verdict"]


@pytest.mark.asyncio
async def test_create_plan_rejects_5k_without_target(monkeypatch):
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
            await main.create_plan(main.PlanCreateRequest(weeks=8, goal_type="5k"), s)
