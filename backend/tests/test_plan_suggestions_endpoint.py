import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.mark.asyncio
async def test_suggestion_flow(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Plan, PlannedWorkout
    import main
    importlib.reload(main)

    now = datetime.utcnow()
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=now - timedelta(days=14), goal_date=now + timedelta(days=42),
                    weeks=8, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        # completed long (4km) 8 days ago + matching run
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=8), week_number=1,
                             day_type="long", target_distance_m=4000, hr_ceiling=165,
                             title="Long", description="x"))
        # missed long 3 days ago (no run)
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=3), week_number=2,
                             day_type="long", target_distance_m=5000, hr_ceiling=165,
                             title="Long", description="x"))
        # oversized upcoming long (6.5km) in 4 days
        big = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=4), week_number=3,
                             day_type="long", target_distance_m=6500, hr_ceiling=165,
                             title="Long", description="x")
        s.add(big)
        s.add(Activity(id=500, name="Run", distance=4000.0, start_date=now - timedelta(days=8),
                       average_speed=1000.0 / 430, average_heartrate=150.0))
        await s.commit()
        big_id = big.id

        sugg = (await main.get_plan_suggestions(s))["suggestions"]
        cap = next((x for x in sugg if x["type"] == "cap_long_run"), None)
        assert cap is not None, f"no cap suggestion in {sugg}"

        resp = await main.apply_plan_suggestion(main.SuggestionApplyRequest(id=cap["id"]), s)
        assert resp["adherence"] is not None
        w = await s.get(PlannedWorkout, big_id)
        assert w.target_distance_m == 5000  # capped: 4.0 completed + 1.0 km
