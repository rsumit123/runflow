"""A watch workout's id is forgotten only once Garmin confirmed it is gone.

Forgetting it while the removal failed leaves an orphan on the runner's watch
that nothing can ever remove again.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

import tenancy
from tests import multiuser_fixtures
from tests.multiuser_fixtures import client, seed_user_b, session_cookie, setup_app

pytestmark = pytest.mark.no_user

STEPS = {"steps": [{"type": "run", "distance_m": 3000}]}


async def _put_on_watch(database, ids, gid=555, structure=None, status=None):
    from models import PlannedWorkout, Plan
    with tenancy.user_context(2):
        async with database.async_session() as s:
            w = await s.get(PlannedWorkout, ids["workout"])
            w.garmin_workout_id = gid
            if structure:
                w.structure = structure
            if status:
                (await s.get(Plan, ids["plan"])).status = status
            await s.commit()


async def _gid(database, ids):
    from models import PlannedWorkout
    with tenancy.user_context(2):
        async with database.async_session() as s:
            w = await s.get(PlannedWorkout, ids["workout"])
            return None if w is None else w.garmin_workout_id


async def test_deleting_a_workout_without_garmin_keeps_it_and_says_why(monkeypatch):
    main, database = await setup_app(monkeypatch, "w1")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.delete(f"/api/plan/workout/{ids['workout']}")
    assert r.status_code == 200
    assert "garmin" in r.json()["garmin_warning"].lower()
    assert await _gid(database, ids) == 555      # row and id both survive


async def test_retiring_a_plan_without_garmin_keeps_the_ids(monkeypatch):
    main, database = await setup_app(monkeypatch, "w2")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.delete(f"/api/plan/{ids['plan']}")
    assert r.status_code == 200 and r.json()["status"] == "abandoned"   # the plan change stands
    assert r.json()["removed_from_watch"] == 0
    assert "garmin" in r.json()["garmin_warning"].lower()
    assert await _gid(database, ids) == 555


async def test_a_failed_removal_in_garmin_push_keeps_the_old_id(monkeypatch):
    main, database = await setup_app(monkeypatch, "w3")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids, structure=STEPS)
    import garmin_auth
    from models import PlannedWorkout

    async def down(_id):
        raise garmin_auth.GarminUnavailable("down")
    async def must_not_push(*a, **k):
        raise AssertionError("pushed a replacement while the old one is still on the watch")
    monkeypatch.setattr(main.garmin, "remove_workout", down)
    monkeypatch.setattr(main.garmin, "push_workout", must_not_push)
    with tenancy.user_context(2):
        async with database.async_session() as s:
            w = await s.get(PlannedWorkout, ids["workout"])
            with pytest.raises(garmin_auth.GarminUnavailable):
                await main._garmin_push(w)
            assert w.garmin_workout_id == 555


async def test_a_failed_removal_on_a_move_to_rest_keeps_the_id_and_warns(monkeypatch):
    main, database = await setup_app(monkeypatch, "w4")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids, structure=STEPS)
    cookie = await session_cookie(database, 2)
    past = (datetime.utcnow() - timedelta(days=3)).date().isoformat()
    async with client(main, cookie) as c:
        r = await c.patch(f"/api/plan/workout/{ids['workout']}", json={"date": past})
    assert r.status_code == 200, r.text
    assert "garmin" in r.json()["garmin_warning"].lower()
    assert await _gid(database, ids) == 555


async def test_pruning_keeps_the_ids_of_what_it_could_not_remove(monkeypatch):
    main, database = await setup_app(monkeypatch, "w5")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids, status="abandoned")
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/plan/prune-watch")
    assert r.status_code == 200 and r.json()["removed"] == 0
    assert "garmin" in r.json()["garmin_warning"].lower()
    assert await _gid(database, ids) == 555


async def test_syncing_the_plan_without_garmin_is_a_409_not_a_200_of_failures(monkeypatch):
    main, database = await setup_app(monkeypatch, "w6")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids, gid=None, structure=STEPS)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.post("/api/plan/sync-to-watch")
    assert r.status_code == 409


async def test_a_vanished_workout_does_not_block_forgetting_it(monkeypatch):
    """Garmin says the id is already gone (404): that is success, not a failure to keep."""
    main, database = await setup_app(monkeypatch, "w7")
    ids = await seed_user_b(database)
    await _put_on_watch(database, ids)
    from garminconnect import GarminConnectConnectionError
    async def gone(_id):
        raise GarminConnectConnectionError("API Error 404 - not found")
    monkeypatch.setattr(main.garmin, "remove_workout", gone)
    cookie = await session_cookie(database, 2)
    async with client(main, cookie) as c:
        r = await c.delete(f"/api/plan/workout/{ids['workout']}")
    assert r.status_code == 200 and r.json() == {"deleted": ids["workout"]}
    assert await _gid(database, ids) is None
