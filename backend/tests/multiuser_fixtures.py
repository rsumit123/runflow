"""Two users with distinct data, and an HTTP client into the real app.

User A's private data carries MARK in every text field we can reach, so a
response containing MARK, served to user B, is a leak.
"""
import re
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import httpx
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute

import tenancy

BACKEND_DIR = Path(__file__).resolve().parent.parent
# config and database are reloaded in place; tenancy holds the ContextVar and the
# session listeners, so replacing it would lose them. auth, credentials and
# auth_routes are NOT kept: they import models inside functions, and dropping
# them is harmless.
_KEEP_MODULES = {"config", "database", "tenancy"}
MARK = "ZZ-USER-A-PRIVATE-7f3c"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
A_ACTIVITY_ID = 9001
B_ACTIVITY_ID = 9002


async def setup_app(monkeypatch, tag):
    """Fresh DB and app. User 1 (admin) is A, user 2 is B. Returns main, database."""
    import importlib, sys, config, database
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=f"{tag}.db"))
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_STATE_SECRET", "test-state-secret")
    monkeypatch.setenv("MCP_SECRET", "")
    # Every first-party module (its file lives in backend/) is dropped and
    # re-imported with the fresh models, except the ones that are reloaded in
    # place or must never be replaced. A hand-written list silently goes stale
    # when a module starts importing models.
    for name, mod in list(sys.modules.items()):
        if name in _KEEP_MODULES or name.split(".")[0] == "tests":
            continue
        f = getattr(mod, "__file__", None)
        if f and Path(f).resolve().parent == BACKEND_DIR:
            sys.modules.pop(name, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401
    await database.init_db()
    import main
    import auth
    import auth_routes
    # auth_routes is never reloaded, so its limiter would carry failures from one test into the next.
    auth_routes.rate_limiter = auth.LoginRateLimiter()
    async with database.async_session() as s:
        await auth.create_user(s, "a@example.com", "a long password", is_admin=True)
        await auth.create_user(s, "b@example.com", "b long password")
        await s.commit()
    return main, database


async def seed_user_a(database, now=None) -> dict:
    """User A's private rows. Returns the ids a cross-tenant probe should try.

    Seed user B FIRST (seed_user_b): then A's plan, workout, goal and label ids
    differ from B's, so a handler that looks a row up by id without the user
    filter would return A's row to B instead of coincidentally B's own.
    """
    from models import (Activity, BestEffort, ChatMessage, DailyWellness, Goal,
                        Plan, PlannedWorkout, RouteLabel, RouteMerge, Split, Stream)
    now = now or datetime.utcnow()
    today = now.date().isoformat()
    with tenancy.user_context(1):
        async with database.async_session() as s:
            s.add(Activity(id=A_ACTIVITY_ID, name=MARK, notes=MARK, sport_type="Run",
                           source="garmin", distance=5000.0, moving_time=1800,
                           elapsed_time=1800, start_date=now - timedelta(days=2),
                           average_speed=2.78, average_heartrate=150.0,
                           start_latlng=[12.97, 77.59], end_latlng=[12.97, 77.59],
                           map_summary_polyline="_p~iF~ps|U_ulLnnqC_mqNvxq`@",
                           has_detailed_data=True, is_interval=True,
                           interval_config={"distance": 400, "result": {
                               "is_interval": True, "segments": [
                                   {"type": "rep", "duration_s": 100 + 3 * i,
                                    "pace_sec_per_km": 250 + 8 * i} for i in range(4)]}}))
            # A second, earlier run: gives A a longer history than B (weekly
            # consistency, counts and trends all differ between the two users).
            s.add(Activity(id=A_ACTIVITY_ID + 2, name=MARK, sport_type="Run",
                           source="garmin", distance=5100.0, moving_time=1850,
                           elapsed_time=1850, start_date=now - timedelta(days=12),
                           average_speed=2.96, has_detailed_data=True,
                           start_latlng=[12.97, 77.59], end_latlng=[12.97, 77.59],
                           map_summary_polyline="_p~iF~ps|U_ulLnnqC_mqNvxq`@"))
            s.add(Split(activity_id=A_ACTIVITY_ID, split_number=1, distance=1000.0,
                        moving_time=360))
            # A real-looking GPS trace (5 km in 30 min), so /intervals has streams to read.
            s.add(Stream(activity_id=A_ACTIVITY_ID, stream_type="time",
                         data=list(range(0, 1801, 5))))
            s.add(Stream(activity_id=A_ACTIVITY_ID, stream_type="distance",
                         data=[t * 5000.0 / 1800 for t in range(0, 1801, 5)]))
            s.add(BestEffort(activity_id=A_ACTIVITY_ID, distance_target=1000,
                             time_seconds=300.0))
            s.add(BestEffort(activity_id=A_ACTIVITY_ID, distance_target=100,
                             time_seconds=17.5))
            # A looped course (4 laps of ~1.2 km) so lap detection finds laps.
            import math
            ds = [t * 5000.0 / 1800 for t in range(0, 1801, 5)]
            s.add(Stream(activity_id=A_ACTIVITY_ID, stream_type="latlng",
                         data=[[12.97 + 0.0018 * math.sin(d / 1257 * 2 * math.pi),
                                77.59 + 0.0018 * (1 - math.cos(d / 1257 * 2 * math.pi))]
                               for d in ds]))
            s.add(ChatMessage(activity_id=A_ACTIVITY_ID, role="user", content=MARK,
                              created_at=now))
            s.add(DailyWellness(date=today, readiness_level=MARK, raw={"note": MARK},
                                fetched_at=now))
            label = RouteLabel(route_key="rk-a", name=MARK)
            goal = Goal(goal_type="volume", weekly_km_target=20.0, mode=MARK,
                        created_at=now, active=True)
            s.add_all([label, goal, RouteMerge(from_key=MARK, to_key="rk-a")])
            plan = Plan(goal_type="5k", goal_distance_m=5000.0, target_time_sec=1800,
                        start_date=now - timedelta(days=7),
                        goal_date=now + timedelta(days=60), weeks=10,
                        status="active", created_at=now, narrative={"overview": MARK})
            s.add(plan)
            await s.flush()
            workout = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=1),
                                     week_number=1, day_type="easy", title=MARK,
                                     description=MARK)
            s.add(workout)
            # A missed long run (no run within a day of it) followed by an upcoming
            # long run that is too big a jump: /api/plan/suggestions then offers A
            # "cap_long_<workout id>", so A's suggestions are not empty.
            s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=6),
                                 week_number=1, day_type="long", title=MARK,
                                 description=MARK, target_distance_m=5000))
            s.add(PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=5),
                                 week_number=2, day_type="long", title=MARK,
                                 description=MARK, target_distance_m=20000))
            await s.commit()
            return {"activity": A_ACTIVITY_ID, "plan": plan.id, "workout": workout.id,
                    "goal": goal.id, "label": label.id, "route_key": "rk-a",
                    "date": today}


async def seed_user_b(database, now=None) -> dict:
    """User B's own activity, plan, workout, goal and route label (seed B first)."""
    from models import Activity, Goal, Plan, PlannedWorkout, RouteLabel
    now = now or datetime.utcnow()
    with tenancy.user_context(2):
        async with database.async_session() as s:
            s.add(Activity(id=B_ACTIVITY_ID, name="b run", sport_type="Run",
                           source="garmin", distance=3000.0, moving_time=1200,
                           elapsed_time=1200, start_date=now - timedelta(days=1),
                           average_speed=2.5))
            label = RouteLabel(route_key="rk-b", name="b route")
            goal = Goal(goal_type="volume", weekly_km_target=10.0, mode="b mode",
                        created_at=now, active=True)
            plan = Plan(goal_type="5k", goal_distance_m=5000.0, target_time_sec=1900,
                        start_date=now - timedelta(days=7),
                        goal_date=now + timedelta(days=60), weeks=10,
                        status="active", created_at=now, narrative={"overview": "b plan"})
            s.add_all([label, goal, plan])
            await s.flush()
            workout = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=1),
                                     week_number=1, day_type="easy", title="b workout",
                                     description="b workout")
            s.add(workout)
            await s.commit()
            return {"activity": B_ACTIVITY_ID, "plan": plan.id, "workout": workout.id,
                    "goal": goal.id, "label": label.id}


async def session_cookie(database, user_id: int) -> str:
    import auth
    async with database.async_session() as s:
        raw = await auth.create_session(s, user_id)
        await s.commit()
    return raw


def client(main, cookie: str | None = None, origin="https://runflow.skdev.one",
           root_path: str = ""):
    headers = {"Origin": origin}
    if cookie:
        headers["Cookie"] = f"rf_session={cookie}"
    # raise_app_exceptions=False: a route that crashes must show up as a 500
    # response the sweep can inspect, not abort the whole sweep.
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False,
                                     root_path=root_path)
    return httpx.AsyncClient(transport=transport,
                             base_url="https://runflow-api.skdev.one",
                             headers=headers, timeout=30)


def api_routes(app):
    """(method, path) for every /api route, straight from the route table."""
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    return out


def fill(path: str, value) -> str:
    return re.sub(r"\{[^}]+\}", str(value), path)


def param_names(path: str) -> list[str]:
    return re.findall(r"\{([^}]+)\}", path)
