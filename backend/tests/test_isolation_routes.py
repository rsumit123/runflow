"""The safety net. Built from the live route table, so a route added later is
covered without anyone remembering to add it here."""
import re
import socket
from pathlib import Path

import pytest
from sqlalchemy import func, select

import tenancy
from tests.multiuser_fixtures import (A_ACTIVITY_ID, MARK, MUTATING, api_routes,
                                      client, fill, seed_user_a, seed_user_b,
                                      session_cookie, setup_app)

pytestmark = pytest.mark.no_user

# The complete list of routes reachable without a session. Pinned here and
# compared with auth_routes.PUBLIC_ROUTES, so widening the allowlist is a
# deliberate, reviewed change to this file.
EXPECTED_PUBLIC = {
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/webhook"),
    ("POST", "/api/webhook"),
    ("GET", "/api/strava/callback"),
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*a, **k):
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


async def test_the_public_allowlist_is_exactly_what_we_expect(monkeypatch):
    await setup_app(monkeypatch, "iso0")
    import auth_routes
    assert auth_routes.PUBLIC_ROUTES == EXPECTED_PUBLIC


async def test_every_api_route_refuses_an_anonymous_caller(monkeypatch):
    main, database = await setup_app(monkeypatch, "iso1")
    assert tenancy.current_user_id() is None  # or in-process trust would make this vacuous
    checked, wrong = 0, []
    async with client(main) as c:
        for method, path in api_routes(main.app):
            if (method, path) in EXPECTED_PUBLIC:
                continue
            r = await c.request(method, fill(path, 1),
                                json={} if method in MUTATING else None)
            checked += 1
            if r.status_code != 401:
                wrong.append(f"{method} {path} -> {r.status_code}")
    assert not wrong, "answered an anonymous caller:\n" + "\n".join(wrong)
    assert checked >= 70, checked


async def test_the_sweep_would_notice_a_leak(monkeypatch):
    """Guard against a vacuous sweep: user A must see MARK somewhere."""
    main, database = await setup_app(monkeypatch, "iso2")
    await seed_user_a(database)
    cookie = await session_cookie(database, 1)
    async with client(main, cookie) as c:
        r = await c.get("/api/activities")
    assert r.status_code == 200 and MARK in r.text


async def test_user_b_cannot_read_anything_of_user_a(monkeypatch):
    main, database = await setup_app(monkeypatch, "iso3")
    ids = await seed_user_a(database)
    await seed_user_b(database)
    cookie = await session_cookie(database, 2)
    candidates = list(dict.fromkeys(str(v) for v in ids.values()))
    leaks = []
    async with client(main, cookie) as c:
        for method, path in api_routes(main.app):
            if method != "GET" or (method, path) in EXPECTED_PUBLIC:
                continue
            urls = [path] if "{" not in path else [fill(path, v) for v in candidates]
            for url in urls:
                r = await c.get(url)
                if MARK in r.text:
                    leaks.append(f"GET {url} -> {r.status_code}")
    assert not leaks, "user B saw user A's data:\n" + "\n".join(leaks)


async def test_user_a_run_is_not_found_for_user_b(monkeypatch):
    main, database = await setup_app(monkeypatch, "iso4")
    await seed_user_a(database)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.get(f"/api/activities/{A_ACTIVITY_ID}")
    assert r.status_code == 404


async def _snapshot(database):
    from models import (Activity, BestEffort, ChatMessage, DailyWellness, Goal,
                        Plan, PlannedWorkout, RouteLabel, RouteMerge, Split, Stream)
    with tenancy.user_context(1):
        async with database.async_session() as s:
            snap = {}
            for model in (Activity, BestEffort, ChatMessage, DailyWellness, Goal, Plan,
                          PlannedWorkout, RouteLabel, RouteMerge, Split, Stream):
                snap[model.__tablename__] = (await s.execute(
                    select(func.count()).select_from(model))).scalar_one()
            a = await s.get(Activity, A_ACTIVITY_ID)
            snap["activity"] = (a.name, a.notes, a.distance)
            w = (await s.execute(select(PlannedWorkout))).scalar_one()
            snap["workout"] = (w.title, w.description, w.date, w.garmin_workout_id)
            p = (await s.execute(select(Plan))).scalar_one()
            snap["plan"] = (p.status, p.target_time_sec)
            g = (await s.execute(select(Goal))).scalar_one()
            snap["goal"] = (g.active, g.mode)
            snap["label"] = (await s.execute(select(RouteLabel.name))).scalar_one()
    return snap


async def test_user_b_cannot_change_or_delete_anything_of_user_a(monkeypatch):
    main, database = await setup_app(monkeypatch, "iso5")
    ids = await seed_user_a(database)
    await seed_user_b(database)
    before = await _snapshot(database)
    cookie = await session_cookie(database, 2)
    candidates = list(dict.fromkeys(str(v) for v in ids.values()))
    body = {"note": "B was here", "name": "B was here", "description": "B was here",
            "date": "2030-01-01", "title": "B was here"}
    async with client(main, cookie) as c:
        for method, path in api_routes(main.app):
            if method not in MUTATING or "{" not in path or (method, path) in EXPECTED_PUBLIC:
                continue
            for v in candidates:
                await c.request(method, fill(path, v), json=body)
    assert await _snapshot(database) == before
    # Nor may B attach rows of its own to A's activity: the endpoints must
    # only reach children through a parent loaded under the caller.
    from models import BestEffort, ChatMessage, Split, Stream
    with tenancy.system_scope():
        async with database.async_session() as s:
            for model in (Split, Stream, BestEffort, ChatMessage):
                n = (await s.execute(select(func.count()).select_from(model).where(
                    model.activity_id == A_ACTIVITY_ID, model.user_id == 2))).scalar_one()
                assert n == 0, f"user B attached a {model.__tablename__} row to A's activity"


async def test_background_tasks_run_as_the_requesting_user(monkeypatch):
    """BackgroundTasks run after the response; they must still be the caller."""
    main, database = await setup_app(monkeypatch, "iso6")
    from fastapi import BackgroundTasks
    seen = []

    async def job():
        seen.append(tenancy.current_user_id())

    @main.app.post("/api/_test_background")
    async def _probe(background_tasks: BackgroundTasks):
        background_tasks.add_task(job)
        return {"ok": True}

    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        assert (await c.post("/api/_test_background")).status_code == 200
    assert seen == [2]


def test_no_raw_sql_in_request_code():
    """Raw SQL bypasses the tenancy filter, so request-serving modules must not use it."""
    here = Path(__file__).resolve().parent.parent
    for name in ("main.py", "mcp_server.py", "mcp_views.py", "mcp_reports.py",
                 "auth_routes.py"):
        src = (here / name).read_text()
        assert "exec_driver_sql" not in src, name
        # text() in any form, literal_column and the lightweight table() are
        # invisible to tenancy.py; tenancy.py's "Known limits" says so.
        for banned in ("text", "literal_column", "table"):
            assert not re.search(rf"from sqlalchemy[\w.]* import[^\n]*\b{banned}\b", src), (name, banned)
            assert f"sqlalchemy.{banned}(" not in src and f"sa.{banned}(" not in src, (name, banned)
