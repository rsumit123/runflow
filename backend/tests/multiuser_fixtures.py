"""Two users with distinct data, and an HTTP client into the real app.

User A's private data carries MARK in every text field we can reach, so a
response containing MARK, served to user B, is a leak.
"""
import re
import tempfile
from datetime import datetime, timedelta

import httpx
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute

import tenancy

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
    for m in ("main", "mcp_server", "models"):
        sys.modules.pop(m, None)
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


async def seed_user_a(database) -> dict:
    """User A's private rows. Returns the ids a cross-tenant probe should try."""
    from models import (Activity, BestEffort, ChatMessage, DailyWellness, Goal,
                        Plan, PlannedWorkout, RouteLabel, RouteMerge, Split, Stream)
    now = datetime.utcnow()
    today = now.date().isoformat()
    with tenancy.user_context(1):
        async with database.async_session() as s:
            s.add(Activity(id=A_ACTIVITY_ID, name=MARK, notes=MARK, sport_type="Run",
                           source="garmin", distance=5000.0, moving_time=1800,
                           elapsed_time=1800, start_date=now - timedelta(days=2),
                           average_speed=2.78, average_heartrate=150.0))
            s.add(Split(activity_id=A_ACTIVITY_ID, split_number=1, distance=1000.0,
                        moving_time=360))
            s.add(Stream(activity_id=A_ACTIVITY_ID, stream_type="time", data=[0, 1, 2]))
            s.add(BestEffort(activity_id=A_ACTIVITY_ID, distance_target=1000,
                             time_seconds=300.0))
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
            await s.commit()
            return {"activity": A_ACTIVITY_ID, "plan": plan.id, "workout": workout.id,
                    "goal": goal.id, "label": label.id, "route_key": "rk-a",
                    "date": today}


async def seed_user_b(database) -> None:
    from models import Activity
    with tenancy.user_context(2):
        async with database.async_session() as s:
            s.add(Activity(id=B_ACTIVITY_ID, name="b run", sport_type="Run",
                           source="garmin", distance=3000.0, moving_time=1200,
                           elapsed_time=1200,
                           start_date=datetime.utcnow() - timedelta(days=1),
                           average_speed=2.5))
            await s.commit()


async def session_cookie(database, user_id: int) -> str:
    import auth
    async with database.async_session() as s:
        raw = await auth.create_session(s, user_id)
        await s.commit()
    return raw


def client(main, cookie: str | None = None, origin="https://runflow.skdev.one"):
    headers = {"Origin": origin}
    if cookie:
        headers["Cookie"] = f"rf_session={cookie}"
    # raise_app_exceptions=False: a route that crashes must show up as a 500
    # response the sweep can inspect, not abort the whole sweep.
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
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
