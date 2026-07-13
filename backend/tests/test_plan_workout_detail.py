import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.mark.asyncio
async def test_workout_detail_done_ran_hard(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Plan, PlannedWorkout, Stream
    import main
    importlib.reload(main)

    now = datetime.utcnow()
    start = now - timedelta(days=5)
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=start, goal_date=now + timedelta(days=50), weeks=8,
                    status="active", created_at=start)
        s.add(plan); await s.flush()
        w = PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=2), week_number=1,
                           day_type="easy", target_distance_m=3000, hr_ceiling=160,
                           title="Easy run", description="conversational")
        s.add(w); await s.flush()
        act = Activity(id=900, name="Morning Run", distance=3000.0,
                       start_date=now - timedelta(days=2), average_speed=1000.0 / 397,
                       average_heartrate=187.0, max_heartrate=205.0,
                       hr_zones=[{"zone": 5, "secs": 800}])
        s.add(act)
        s.add(Stream(activity_id=900, stream_type="heartrate", data=[150, 180, 187, 190] * 400))
        await s.commit()

        detail = await main.plan_workout_detail(w.id, s)
        assert detail["workout"]["status"] == "done"
        assert detail["workout"]["compliance"] == "ran_hard"
        assert detail["actual"]["avg_hr"] == 187.0
        assert detail["actual"]["hr_zones"] == [{"zone": 5, "secs": 800}]
        assert len(detail["actual"]["heartrate"]) <= 120  # downsampled
        assert "ceiling" in detail["verdict"].lower()
        assert detail["plan"]["weeks"] == 8


@pytest.mark.asyncio
async def test_workout_detail_upcoming(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Plan, PlannedWorkout
    import main
    importlib.reload(main)

    now = datetime.utcnow()
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=now, goal_date=now + timedelta(days=50), weeks=8,
                    status="active", created_at=now)
        s.add(plan); await s.flush()
        w = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=3), week_number=1,
                           day_type="long", target_distance_m=5000, hr_ceiling=165,
                           title="Long run", description="steady")
        s.add(w); await s.commit()
        detail = await main.plan_workout_detail(w.id, s)
        assert detail["workout"]["status"] == "upcoming"
        assert detail["actual"] is None
        assert "coming up" in detail["verdict"].lower()
