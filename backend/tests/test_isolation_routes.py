"""The safety net. Built from the live route table, so a route added later is
covered without anyone remembering to add it here."""
import itertools
import json
import re
import socket
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

import tenancy
from tests.multiuser_fixtures import (A_ACTIVITY_ID, MARK, MUTATING, api_routes,
                                      client, fill, param_names, seed_user_a, seed_user_b,
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


async def test_every_api_route_refuses_an_anonymous_caller_behind_a_root_path(monkeypatch):
    """uvicorn --root-path puts the prefix in scope["path"]; Starlette routes on the
    path with it stripped. The middleware must judge the path the router will see."""
    main, database = await setup_app(monkeypatch, "iso1r")
    checked, wrong = 0, []
    async with client(main, root_path="/x") as c:
        for method, path in api_routes(main.app):
            if (method, path) in EXPECTED_PUBLIC:
                continue
            r = await c.request(method, "/x" + fill(path, 1),
                                json={} if method in MUTATING else None)
            checked += 1
            if r.status_code != 401:
                wrong.append(f"{method} /x{path} -> {r.status_code}")
    assert not wrong, "answered an anonymous caller behind a root_path:\n" + "\n".join(wrong)
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
    await seed_user_b(database)
    ids = await seed_user_a(database)
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
    await seed_user_b(database)
    ids = await seed_user_a(database)
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


# ---------------------------------------------------------------------------
# Sweeps that cannot pass vacuously.
#
# "B sees no MARK" alone proves nothing for a route whose response never
# contains MARK even for A, and a 500 or a 422 looks like a pass. These sweeps
# therefore compare B's responses with and without user A in the database,
# compare B's response to A's ids with its response to an id that does not
# exist, require every GET route to give A a 2xx, and refuse any 5xx that is
# not explained below.
# ---------------------------------------------------------------------------

NONEXISTENT = 999999
FIXED_NOW = datetime.utcnow()

# Query parameters a route needs before it will do anything (else it 422s for
# every caller and the sweep would never reach the handler's data access).
GET_QUERY = {
    "/api/activities/{activity_id}/intervals": {"reps": 5, "distance": 400},
}

# Path parameters that are not row ids: the value is the same for every user.
NON_ID_PARAMS = {"distance": [400, 1000]}

# Volatile response fields, normalised before comparing. Empty on purpose: add
# an entry only with a reason naming the field, e.g.
#   "/api/some/route": ("generated_at", "wall-clock time of the response"),
VOLATILE: dict[str, tuple[str, str]] = {}

# GET routes user A cannot get a 2xx from, with the reason. A stale entry (the
# route now answers 2xx) fails the test, so this list can only shrink.
UNREACHABLE: dict[str, str] = {}

# GET routes whose answer is identical for A and B even though A has data B lacks,
# so "B's answer is unchanged by A" cannot detect a leak there. Each needs a reason.
SAME_FOR_A_AND_B: dict[str, str] = {
    "/api/plan/suggestions":
        "Suggestions are derived from plan adherence (completed vs planned workouts over "
        "weeks); the seed does not simulate that history, so both users get []. Plan "
        "lookups by id are covered by /api/plan/workout/{workout_id} and the mutating "
        "sweep (apply is probed).",
    "/api/sync/status":
        "Process-wide auto-sync timestamps, not per-user data. Task 7 makes it per-user.",
}

# Routes allowed to answer 5xx in a sweep, with the reason.
ALLOWED_5XX: dict[tuple[str, str], str] = {
    ("GET", "/api/plan/today-guidance"):
        "User B has no cached wellness for today and no Garmin connection, so the route "
        "reaches garmin.wellness() and raises RuntimeError('Garmin token store empty'). "
        "Task 6 turns 'Garmin not connected' into a 409; remove this entry then.",
    ("DELETE", "/api/workouts/adhoc/{workout_id}"):
        "The id is a Garmin Connect workout id, not a database row. Today the route calls "
        "the one process-wide Garmin client, which has no token store in tests, so it 502s. "
        "Until Task 6 gives each user their own Garmin client, any signed-in user could "
        "ask the OWNER's Garmin account to delete a workout. Task 6 makes it 409 for a "
        "user with no Garmin connection; remove this entry then.",
    ("POST", "/api/import/garmin/sync"):
        "Fetches from the process-wide Garmin client, which has no token store in tests "
        "(502). Task 6 makes this per-user and 409 when Garmin is not connected.",
}

# Mutating routes with a path id that may answer something other than 4xx to B
# for A's id, with the reason. Their answer must still be identical to the one for
# a nonexistent id, and A's data must be unchanged.
ALLOWED_NON_4XX_FOR_FOREIGN_ID: dict[tuple[str, str], str] = {
    ("POST", "/api/best-efforts/compute/{activity_id}"):
        "Computes from the caller's own streams for that id and returns an empty list when "
        "there are none; it never 404s, for any id. Identical to a nonexistent id.",
    ("DELETE", "/api/activities/{activity_id}/chat"):
        "Deletes the caller's own chat messages for that activity id and reports a count "
        "(0); it never 404s, for any id. Identical to a nonexistent id.",
    ("DELETE", "/api/workouts/adhoc/{workout_id}"):
        "The id is a Garmin Connect workout id, not a database row (see ALLOWED_5XX).",
}

# Mutating routes with a path id for which every probe is a 422 (the handler is
# never reached), with the reason.
UNEXERCISED: dict[tuple[str, str], str] = {}


def _id_candidates(ids: dict) -> dict[str, list]:
    return {"activity_id": [ids["activity"]], "workout_id": [ids["workout"]],
            "plan_id": [ids["plan"]], "goal_id": [ids["goal"]], **NON_ID_PARAMS}


def _urls(path: str, ids: dict) -> list[tuple[str, object | None]]:
    """(url, candidate) for a route; candidate is None for a parameterless route."""
    names = param_names(path)
    if not names:
        return [(path, None)]
    assert len(names) == 1, path
    cands = _id_candidates(ids)
    assert names[0] in cands, f"no candidate values for {{{names[0]}}} in {path}"
    return [(fill(path, v), v) for v in cands[names[0]]]


def _get_routes(main):
    return [(m, p) for m, p in api_routes(main.app)
            if m == "GET" and (m, p) not in EXPECTED_PUBLIC]


def _normalise(path: str, text: str, candidate) -> str:
    if candidate is not None:
        # Some 404s echo the id they were asked for.
        text = text.replace(str(candidate), "<ID>")
    if path in VOLATILE:
        field, _reason = VOLATILE[path]
        try:
            body = json.loads(text)
            if isinstance(body, dict):
                body.pop(field, None)
            text = json.dumps(body, sort_keys=True)
        except ValueError:
            pass
    return text


async def _get(c, path, url):
    r = await c.get(url, params=GET_QUERY.get(path))
    return r.status_code, r.text


def _check_5xx(results: dict, who: str):
    bad = [f"{k}: {v[0]}" for k, v in results.items() if v[0] >= 500
           and (k[0], k[1]) not in ALLOWED_5XX]
    assert not bad, f"{who} got 5xx (a crash looks like a pass):\n" + "\n".join(bad)


async def _collect_b(monkeypatch, tag, with_a):
    """B's GET responses to every route, and to A's ids, in a fresh app."""
    main, database = await setup_app(monkeypatch, tag)
    await seed_user_b(database, FIXED_NOW)
    ids = await seed_user_a(database, FIXED_NOW) if with_a else None
    return main, database, ids


async def _b_responses(main, database, ids):
    cookie = await session_cookie(database, 2)
    out = {}
    async with client(main, cookie) as c:
        for method, path in _get_routes(main):
            for url, cand in _urls(path, ids):
                status, text = await _get(c, path, url)
                out[(method, path, str(cand))] = (status, _normalise(path, text, cand))
            if any(n not in NON_ID_PARAMS for n in param_names(path)):
                url = fill(path, NONEXISTENT)
                status, text = await _get(c, path, url)
                out[(method, path, "nonexistent")] = (status, _normalise(path, text, NONEXISTENT))
    return out


async def test_user_b_gets_the_same_answers_with_or_without_user_a(monkeypatch):
    # App 1: A and B both seeded (B first, so their ids differ).
    main, database, ids = await _collect_b(monkeypatch, "iso7a", with_a=True)
    with_a = await _b_responses(main, database, ids)
    # App 2: only B, identical B data. A's ids simply do not exist there.
    main, database, _ = await _collect_b(monkeypatch, "iso7b", with_a=False)
    without_a = await _b_responses(main, database, ids)

    assert set(with_a) == set(without_a)
    assert len(with_a) >= 40, len(with_a)  # not vacuous: every GET route was probed
    _check_5xx(with_a, "user B")
    _check_5xx(without_a, "user B (no A present)")

    leaks = [f"{k}: B saw A's mark" for k, (_, text) in with_a.items() if MARK in text]
    # (a) the same B request must get the same answer whether or not A exists.
    leaks += [f"{k}: differs when user A exists:\n  with A:    {with_a[k]}\n  without A: {without_a[k]}"
              for k in with_a if with_a[k] != without_a[k]]
    # (b) A's id must look exactly like an id that does not exist.
    for (method, path, cand), resp in with_a.items():
        if cand in ("nonexistent", "None") or any(n in NON_ID_PARAMS for n in param_names(path)):
            continue
        if resp != with_a[(method, path, "nonexistent")]:
            leaks.append(f"{method} {path} {cand}: B's answer for A's id differs from "
                         f"its answer for a nonexistent id:\n  A's id:       {resp}\n"
                         f"  nonexistent: {with_a[(method, path, 'nonexistent')]}")
    assert not leaks, "\n".join(leaks)


async def test_every_get_route_gives_its_owner_a_2xx(monkeypatch):
    """Anti-vacuity: if A cannot reach a route, 'B cannot see A's data there' is empty."""
    main, database, ids = await _collect_b(monkeypatch, "iso8", with_a=True)
    cookie = await session_cookie(database, 1)
    ok: dict[str, bool] = {}
    differs: dict[str, bool] = {}
    codes: dict = {}
    b_cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c, client(main, b_cookie) as cb:
        for method, path in _get_routes(main):
            ok[path] = differs[path] = False
            for url, cand in _urls(path, ids):
                status, text = await _get(c, path, url)
                codes[(method, path, str(cand))] = (status, text)
                ok[path] = ok[path] or 200 <= status < 300
                if 200 <= status < 300:
                    differs[path] = differs[path] or (status, text) != await _get(cb, path, url)
    _check_5xx(codes, "user A")
    unreachable = sorted(p for p, good in ok.items() if not good)
    assert unreachable == sorted(UNREACHABLE), (
        "GET routes user A cannot get a 2xx from must be listed in UNREACHABLE with a "
        f"reason (and stale entries removed): {unreachable}")
    # Where A's answer is no different from B's, a leak could not show up either.
    same = sorted(p for p, good in ok.items() if good and not differs[p])
    assert same == sorted(SAME_FOR_A_AND_B), (
        "GET routes where A and B get identical answers must be listed in "
        f"SAME_FOR_A_AND_B with a reason (and stale entries removed): {same}")


# ---------------------------------------------------------------------------
# Mutating sweep
# ---------------------------------------------------------------------------

# A valid request body for each mutating route, naming A's route key and
# carrying B's marker text, so a handler that reaches A's row and writes to it
# changes something the snapshot sees. Anything not listed gets {}.
MUTATING_BODIES = {
    ("POST", "/api/plan/sprint/baseline"): {"time_sec": 14.0, "note": "B was here"},
    ("POST", "/api/plan"): {"weeks": 8, "goal_type": "5k", "target_time_sec": 1800},
    ("PATCH", "/api/plan/workout/{workout_id}"): {"date": "2030-01-01"},
    ("POST", "/api/plan/suggestions/apply"): {"id": "anything"},
    ("PUT", "/api/activities/{activity_id}/notes"): {"note": "B was here"},
    ("POST", "/api/workouts/adhoc"): {"name": "B was here", "date": "2030-01-01", "steps": []},
    ("POST", "/api/routes/label"): {"route_key": "rk-a", "name": "B was here"},
    ("POST", "/api/routes/merge"): {"source_route_key": "rk-a", "target_route_key": "rk-b"},
    ("POST", "/api/goals"): {"goal_type": "volume", "weekly_km_target": 5.0, "mode": "B was here"},
    ("POST", "/api/activities/{activity_id}/mark-interval"):
        {"is_interval": True, "interval_config": {"distance": 400}},
    ("POST", "/api/import/by-date"): {"start_date": "2020-01-01", "end_date": "2020-01-02"},
    ("POST", "/api/import/bulk"): {"directory": "/etc"},
    ("POST", "/api/activities/{activity_id}/chat"): {"message": "B was here"},
    ("PATCH", "/api/plan/workout/{workout_id}/note"): {"note": "B was here"},
    ("POST", "/api/auth/signup"): {},
}


def _mutating_routes(main):
    return [(m, p) for m, p in api_routes(main.app)
            if m in MUTATING and (m, p) not in EXPECTED_PUBLIC]


async def _snapshot_all_of_a(database) -> dict:
    """Every column of every row A owns, in every per-user table."""
    import models
    out = {}
    with tenancy.system_scope():
        async with database.async_session() as s:
            for mapper in models.Base.registry.mappers:
                cls = mapper.class_
                if not issubclass(cls, tenancy.TenantMixin):
                    continue
                rows = (await s.execute(select(cls).where(cls.user_id == 1))).scalars().all()
                out[cls.__tablename__] = sorted(
                    repr([(c.key, getattr(r, c.key)) for c in mapper.column_attrs])
                    for r in rows)
    return out


async def test_user_b_mutating_requests_leave_all_of_user_a_unchanged(monkeypatch):
    main, database = await setup_app(monkeypatch, "iso9")
    await seed_user_b(database, FIXED_NOW)
    ids = await seed_user_a(database, FIXED_NOW)
    before = await _snapshot_all_of_a(database)
    assert sum(len(v) for v in before.values()) >= 12  # the snapshot really holds A's rows
    cookie = await session_cookie(database, 2)
    cands = _id_candidates(ids)
    results: dict = {}
    async with client(main, cookie) as c:
        for method, path in _mutating_routes(main):
            body = MUTATING_BODIES.get((method, path), {})
            names = param_names(path)
            if names:
                assert names[0] in cands, path
            for url, cand in (_urls(path, ids) if names else [(path, None)]):
                r = await c.request(method, url, json=body)
                results[(method, path, str(cand))] = (r.status_code, r.text)
            if names and names[0] not in NON_ID_PARAMS:
                r = await c.request(method, fill(path, NONEXISTENT), json=body)
                results[(method, path, "nonexistent")] = (r.status_code, r.text)

    assert await _snapshot_all_of_a(database) == before

    _check_5xx(results, "user B (mutating)")
    wrong = []
    for (method, path, cand), (status, text) in results.items():
        if MARK in text:
            wrong.append(f"{method} {path} {cand}: response carried A's mark")
        if cand in ("None", "nonexistent"):
            continue
        if (method, path) not in ALLOWED_NON_4XX_FOR_FOREIGN_ID and not 400 <= status < 500:
            wrong.append(f"{method} {path} {cand}: B got {status} for A's id, expected 4xx")
        other = results[(method, path, "nonexistent")]
        if (status, _normalise(path, text, cand)) != (other[0], _normalise(path, other[1], NONEXISTENT)):
            wrong.append(f"{method} {path} {cand}: B's answer for A's id differs from its "
                         f"answer for a nonexistent id: {(status, text)} vs {other}")
    for (method, path) in {(m, p) for m, p, c in results if c not in ("None", "nonexistent")}:
        seen = {results[k][0] for k in results if k[:2] == (method, path)}
        if (method, path) not in UNEXERCISED and seen == {422}:
            wrong.append(f"{method} {path}: every probe was a 422, so the handler was never "
                         "reached; fix the body in MUTATING_BODIES")
    assert not wrong, "\n".join(wrong)
    # Every mutating route was probed at least once.
    assert len({k[:2] for k in results}) == len(_mutating_routes(main))
