import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.mark.asyncio
async def test_move_workout_and_regroup(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Plan, PlannedWorkout
    import main
    importlib.reload(main)

    start = datetime(2026, 7, 13)  # Monday
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=start, goal_date=start + timedelta(days=56), weeks=8,
                    status="active", created_at=start)
        s.add(plan)
        await s.flush()
        easy = PlannedWorkout(plan_id=plan.id, date=datetime(2026, 7, 16), week_number=1,
                              day_type="easy", target_distance_m=3500, hr_ceiling=160,
                              title="Easy run", description="x")
        quality = PlannedWorkout(plan_id=plan.id, date=datetime(2026, 7, 29), week_number=3,
                                 day_type="quality", target_distance_m=4000, title="Speed", description="x")
        longw = PlannedWorkout(plan_id=plan.id, date=datetime(2026, 7, 30), week_number=3,
                               day_type="long", target_distance_m=5000, title="Long", description="x")
        s.add_all([easy, quality, longw])
        await s.commit()
        easy_id, quality_id = easy.id, quality.id

        # Move the easy run from Jul 16 -> Jul 14 (same week, no warning)
        resp = await main.move_workout(easy_id, main.WorkoutMoveRequest(date="2026-07-14"), s)
        assert resp["warning"] is None
        moved = await s.get(PlannedWorkout, easy_id)
        assert moved.date.date().isoformat() == "2026-07-14"
        assert moved.week_number == 1

        # Move the quality session next to the long run -> back-to-back-hard warning
        resp2 = await main.move_workout(quality_id, main.WorkoutMoveRequest(date="2026-07-31"), s)
        assert resp2["warning"] is not None and "hard" in resp2["warning"].lower()


@pytest.mark.asyncio
async def test_move_bad_date(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Plan, PlannedWorkout
    import main
    importlib.reload(main)
    from fastapi import HTTPException

    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=datetime(2026, 7, 13), goal_date=datetime(2026, 9, 5),
                    weeks=8, status="active", created_at=datetime(2026, 7, 13))
        s.add(plan); await s.flush()
        w = PlannedWorkout(plan_id=plan.id, date=datetime(2026, 7, 16), week_number=1,
                           day_type="easy", target_distance_m=3000, title="Easy", description="x")
        s.add(w); await s.commit()
        with pytest.raises(HTTPException):
            await main.move_workout(w.id, main.WorkoutMoveRequest(date="not-a-date"), s)
