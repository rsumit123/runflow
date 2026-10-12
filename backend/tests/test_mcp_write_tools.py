"""The nine write tools.

Each is a thin wrapper over an endpoint that already holds the logic, so these
tests are about the wrapper's job: validating arguments, rendering what
actually changed, and never reporting a plan change and a watch change as the
same thing.
"""
import tempfile
from datetime import datetime, timedelta

import pytest


async def _app(monkeypatch, tag):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, sys, config, database
    for _m in ("main", "mcp_server", "models"):
        sys.modules.pop(_m, None)
    importlib.reload(config); importlib.reload(database)
    import models  # noqa: F401 — populates Base.metadata before init_db
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)
    return main, mcp_server, database


async def _seed_plan(database, *, start_offset_days=0, n_sessions=3,
                     on_watch=False, with_steps=True):
    """An active 12-week plan with n_sessions upcoming workouts, ids 1..n.

    on_watch gives each a garmin_workout_id so unpush/move have something to
    take off the watch. with_steps controls whether they are pushable at all.
    """
    from models import Plan, PlannedWorkout
    now = datetime.utcnow()
    start = (now + timedelta(days=start_offset_days)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    steps = {"steps": [{"type": "run", "end_kind": "distance",
                        "end_value": 3000, "target_kind": "pace",
                        "pace_low_sec": 435, "pace_high_sec": 450}]}
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1800,
                    start_date=start, goal_date=start + timedelta(days=84),
                    weeks=12, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        for i in range(1, n_sessions + 1):
            s.add(PlannedWorkout(
                id=i, plan_id=plan.id, date=start + timedelta(days=i),
                week_number=1, day_type="easy" if i < n_sessions else "long",
                target_distance_m=3000 + 500 * i, pace_low_sec=435,
                pace_high_sec=450, hr_ceiling=160, title=f"Session {i}",
                description="Keep it conversational. Take walk breaks.",
                structure=steps if with_steps else None,
                garmin_workout_id=(1000 + i) if on_watch else None))
        await s.commit()
        return plan.id


def _no_raw_http(out: str) -> bool:
    """True when no raw HTTPException str() leaked into the response.

    str(HTTPException(404, "Workout not found")) is '404: Workout not found',
    so a class-name check cannot catch this — the status code and colon can.
    """
    return not any(f"{code}: " in out for code in (400, 404, 422, 500, 502))


@pytest.mark.asyncio
async def test_set_workout_note_replaces_the_description(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn")
    await _seed_plan(database)
    from models import PlannedWorkout

    out = await mcp_server.set_workout_note(
        workout_id=1, note="Continuous only — no walk breaks.")

    assert "Continuous only" in out
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w.description == "Continuous only — no walk breaks."


@pytest.mark.asyncio
async def test_set_workout_note_on_a_missing_workout_is_readable(monkeypatch):
    """The endpoint raises HTTPException; a bare except would print its repr."""
    main, mcp_server, database = await _app(monkeypatch, "wn2")
    await _seed_plan(database)

    out = await mcp_server.set_workout_note(workout_id=9999, note="x")

    assert "not found" in out.lower()
    assert "Traceback" not in out
    # str(HTTPException) is "404: Workout not found" — no class name in it, so a
    # class-name check cannot catch a bare `except Exception`.
    assert _no_raw_http(out), "a status code leaking in means detail was not rendered"


@pytest.mark.asyncio
async def test_set_workout_note_rejects_an_overlong_note(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn3")
    await _seed_plan(database)
    import config
    from models import PlannedWorkout

    out = await mcp_server.set_workout_note(
        workout_id=1, note="x" * (config.NOTE_MAX_CHARS + 1))

    assert "too long" in out.lower()
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert "walk breaks" in w.description, "nothing should have changed"


@pytest.mark.asyncio
async def test_set_workout_note_clearing_reads_as_cleared(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "wn4")
    await _seed_plan(database)

    out = await mcp_server.set_workout_note(workout_id=1, note="")

    assert "clear" in out.lower(), f"an empty note should read as cleared: {out}"


@pytest.mark.asyncio
async def test_move_workout_reports_the_new_date_and_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv")
    await _seed_plan(database, on_watch=True)
    pushed = []

    async def fake_push(w):
        pushed.append(w.id)
        w.garmin_workout_id = 2001
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    target = (datetime.utcnow() + timedelta(days=9)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    assert target in out
    assert pushed == [1], "a future move must re-push to the watch"
    assert "watch" in out.lower(), f"the watch outcome must be stated: {out}"
    assert "not" not in out.lower(), f"a successful move must not read as a failure: {out}"
    assert "Session 1" in out


@pytest.mark.asyncio
async def test_move_workout_says_the_watch_was_not_updated_when_garmin_fails(monkeypatch):
    """The move is saved and the watch is stale. Those must not read the same.

    If the tool says "moved to Friday" while Garmin still holds Thursday, the
    athlete runs the wrong session on the wrong day. Note the workout KEEPS its
    old garmin_workout_id here (the endpoint does not clear it on a failed
    re-push), so a clause keyed on the id alone would say "on the watch".
    """
    main, mcp_server, database = await _app(monkeypatch, "mv2")
    await _seed_plan(database, on_watch=True)
    from models import PlannedWorkout

    async def broken(w):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main, "_garmin_push", broken)

    target = (datetime.utcnow() + timedelta(days=9)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w.date.date().isoformat() == target, "the move itself must persist"
        assert w.garmin_workout_id == 1001, "precondition: the stale id is still set"

    low = out.lower()
    assert "not" in low and "watch" in low, (
        f"must say the watch was NOT updated, got: {out}"
    )
    assert target in out, "the saved move should still be reported"
    assert "old" in low or "previous" in low, f"must say the watch holds the old version: {out}"


@pytest.mark.asyncio
async def test_move_workout_into_the_past_is_not_called_a_failure(monkeypatch):
    """Deliberately taken off the watch is a different fact from a failed push."""
    main, mcp_server, database = await _app(monkeypatch, "mv2b")
    await _seed_plan(database, on_watch=True)
    removed = []

    async def fake_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    target = (datetime.utcnow() - timedelta(days=3)).date().isoformat()
    out = await mcp_server.move_workout(workout_id=1, date=target)

    assert removed == [1001]
    low = out.lower()
    assert "past" in low, f"the reason must be named: {out}"
    assert "failed" not in low and "still" not in low, f"not a failure: {out}"


@pytest.mark.asyncio
async def test_move_workout_surfaces_the_back_to_back_warning(monkeypatch):
    """The endpoint warns without blocking; the tool must not swallow it."""
    main, mcp_server, database = await _app(monkeypatch, "mv3")
    await _seed_plan(database, n_sessions=3)

    async def fake_push(w):
        w.garmin_workout_id = 2002
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    from models import PlannedWorkout
    async with database.async_session() as s:
        hard = await s.get(PlannedWorkout, 3)       # day_type "long"
        hard_date = hard.date.date()

    # Move session 1 to the day before the long run, and make it hard too.
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        w.day_type = "quality"
        await s.commit()

    out = await mcp_server.move_workout(
        workout_id=1, date=(hard_date - timedelta(days=1)).isoformat())

    assert "hard" in out.lower(), f"the back-to-back warning must appear: {out}"


@pytest.mark.asyncio
async def test_move_workout_rejects_a_malformed_date(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv4")
    await _seed_plan(database)

    out = await mcp_server.move_workout(workout_id=1, date="next Tuesday")

    assert "yyyy-mm-dd" in out.lower() or "iso" in out.lower()
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_move_workout_on_a_missing_workout_is_readable(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "mv5")
    await _seed_plan(database)

    out = await mcp_server.move_workout(
        workout_id=9999, date=(datetime.utcnow() + timedelta(days=3)).date().isoformat())

    assert "not found" in out.lower()
    assert _no_raw_http(out)


def test_watch_clause_three_branches():
    import mcp_server
    wc = mcp_server._watch_clause

    on = wc(garmin_workout_id=7, eligible=True, push_ok=True).lower()
    assert "updated" in on and "watch" in on and "not" not in on

    # Eligible, push failed, but a STALE id is still set: must read as failure.
    stale = wc(garmin_workout_id=7, eligible=True, push_ok=False).lower()
    assert "not" in stale and "watch" in stale and "old" in stale

    # Eligible, no id, push outcome unknown: still a failure, not "no need".
    missing = wc(garmin_workout_id=None, eligible=True).lower()
    assert "not" in missing and "old" not in missing and "need" not in missing

    # Not eligible: not on the watch, and that is fine, with the reason.
    for reason in ("it is in the past", "it is a rest day", "it has no structured steps"):
        off = wc(garmin_workout_id=None, eligible=False, reason=reason).lower()
        assert reason in off and "need" in off and "failed" not in off


@pytest.mark.asyncio
async def test_push_workout_puts_one_session_on_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "pw")
    await _seed_plan(database)
    pushed = []

    async def fake_push(w):
        pushed.append(w.id)
        w.garmin_workout_id = 3001
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    out = await mcp_server.push_workout(workout_id=1)

    assert pushed == [1]
    assert "Session 1" in out, "name which session, not just the id"
    expected_date = (datetime.utcnow() + timedelta(days=1)).date().isoformat()
    assert expected_date in out, f"name the date the session is scheduled: {out}"
    assert "has been updated and holds this session" in out, (
        f"success must come from the endpoint's verdict: {out}")
    from models import PlannedWorkout
    async with database.async_session() as s:
        assert (await s.get(PlannedWorkout, 1)).garmin_workout_id == 3001


@pytest.mark.asyncio
async def test_push_workout_with_no_steps_says_never_and_does_not_call_garmin(monkeypatch):
    """400 is permanent: the athlete should stop asking, not retry."""
    main, mcp_server, database = await _app(monkeypatch, "pw2")
    await _seed_plan(database, with_steps=False)
    called = []

    async def fake_push(w):
        called.append(w.id)
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    out = await mcp_server.push_workout(workout_id=1)

    assert called == [], "no steps means Garmin is never asked"
    assert "step" in out.lower(), f"say it has no steps: {out}"
    assert "try again" not in out.lower() and "retry" not in out.lower(), (
        f"a permanent condition must not invite a retry: {out}")
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_push_workout_when_garmin_is_down_says_retry_and_name_garmin(monkeypatch):
    """502 is transient: name Garmin, say nothing reached the watch, invite a retry."""
    main, mcp_server, database = await _app(monkeypatch, "pw3")
    await _seed_plan(database, with_steps=True)

    async def broken(w):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main, "_garmin_push", broken)

    out = await mcp_server.push_workout(workout_id=1)

    assert "garmin" in out.lower(), f"name Garmin: {out}"
    assert "garmin 503" in out, f"carry the underlying cause: {out}"
    assert "not on the watch" in out.lower() or "not been sent" in out.lower(), (
        f"say the session did not reach the watch: {out}")
    assert "try again" in out.lower(), f"a transient failure invites a retry: {out}"
    assert "step" not in out.lower(), f"must not read like the no-steps case: {out}"
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_push_workout_unknown_id_says_so(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "pw4")
    await _seed_plan(database)

    out = await mcp_server.push_workout(workout_id=999)

    assert "not found" in out.lower()
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_unpush_workout_takes_it_off_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "up")
    await _seed_plan(database, on_watch=True)
    removed = []

    async def fake_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    out = await mcp_server.unpush_workout(workout_id=1)

    assert removed == [1001], "the stored garmin id must be the one removed"
    from models import PlannedWorkout
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w.garmin_workout_id is None
    assert "Session 1" in out
    assert "taken off the watch" in out.lower(), f"say it was removed: {out}"
    assert "still in the plan" in out.lower(), f"say the plan is untouched: {out}"


@pytest.mark.asyncio
async def test_unpush_a_workout_that_was_never_on_the_watch_says_so(monkeypatch):
    """Not an error, and not a removal either — the state was already correct."""
    main, mcp_server, database = await _app(monkeypatch, "up2")
    await _seed_plan(database, on_watch=False)
    removed = []

    async def fake_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    out = await mcp_server.unpush_workout(workout_id=1)

    assert removed == [], "nothing to remove, so Garmin must not be called"
    low = out.lower()
    assert "was not on the watch" in low, (
        f"say it was already not on the watch: {out}")
    assert "taken off" not in low and "removed" not in low, (
        f"must not claim a removal that did not happen: {out}")
    assert "Session 1" in out


@pytest.mark.asyncio
async def test_unpush_when_garmin_fails_says_it_is_still_on_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "up4")
    await _seed_plan(database, on_watch=True)

    async def broken(gid):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main.garmin, "remove_workout", broken)

    out = await mcp_server.unpush_workout(workout_id=1)

    low = out.lower()
    assert "still on the watch" in low, f"nothing was removed: {out}"
    assert "taken off" not in low
    assert "garmin 503" in out
    assert "Traceback" not in out and _no_raw_http(out)
    from models import PlannedWorkout
    async with database.async_session() as s:
        assert (await s.get(PlannedWorkout, 1)).garmin_workout_id == 1001


@pytest.mark.asyncio
async def test_unpush_unknown_id_says_so(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "up5")
    await _seed_plan(database)

    out = await mcp_server.unpush_workout(workout_id=999)

    assert "not found" in out.lower()
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_unpush_keeps_the_session_in_the_plan(monkeypatch):
    """Removing from the watch must never delete the planned session."""
    main, mcp_server, database = await _app(monkeypatch, "up3")
    await _seed_plan(database, on_watch=True)

    async def fake_remove(gid):
        pass
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    await mcp_server.unpush_workout(workout_id=1)

    from models import PlannedWorkout
    async with database.async_session() as s:
        w = await s.get(PlannedWorkout, 1)
        assert w is not None, "the session must still exist in the plan"
        assert w.title == "Session 1"


@pytest.mark.asyncio
async def test_sync_plan_to_watch_names_the_sessions_that_failed(monkeypatch):
    """A bare count does not tell him which runs his watch will not guide."""
    main, mcp_server, database = await _app(monkeypatch, "sy")
    await _seed_plan(database, n_sessions=4)

    async def flaky(w):
        if w.id == 2:
            raise RuntimeError("garmin rejected step 3")
        w.garmin_workout_id = 4000 + w.id
    monkeypatch.setattr(main, "_garmin_push", flaky)

    out = await mcp_server.sync_plan_to_watch()

    assert "Session 2" in out, f"name the failed session: {out}"
    assert "garmin rejected step 3" in out, "carry the reason"
    for ok_title in ("Session 1", "Session 3", "Session 4"):
        assert ok_title not in out, f"{ok_title} succeeded and must not be listed"
    assert "Sent 3 sessions" in out, f"state exactly how many succeeded: {out}"
    assert "1 failed" in out, f"state exactly how many failed: {out}"
    assert "have not been sent" in out, "never claim the failed one is on the watch"


@pytest.mark.asyncio
async def test_sync_plan_to_watch_with_no_active_plan_is_a_sentence(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "sy2")

    out = await mcp_server.sync_plan_to_watch()

    assert "no active plan" in out.lower()
    assert _no_raw_http(out) and "Traceback" not in out


@pytest.mark.asyncio
async def test_sync_plan_to_watch_reports_a_clean_run(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "sy3")
    await _seed_plan(database, n_sessions=3)

    async def ok(w):
        w.garmin_workout_id = 4100 + w.id
    monkeypatch.setattr(main, "_garmin_push", ok)

    out = await mcp_server.sync_plan_to_watch()

    assert "Sent 3 sessions" in out, out
    assert "No failures" in out, out
    assert "failed" not in out.lower().replace("no failures", "")
    assert "only upcoming" in out.lower(), "say only upcoming structured sessions are sent"


@pytest.mark.asyncio
async def test_sync_plan_to_watch_with_nothing_eligible_says_so(monkeypatch):
    """Unstructured sessions are skipped silently by the endpoint; that is not
    a success and must not read as one."""
    main, mcp_server, database = await _app(monkeypatch, "sy5")
    await _seed_plan(database, n_sessions=2, with_steps=False)

    async def boom(w):
        raise AssertionError("must not be pushed")
    monkeypatch.setattr(main, "_garmin_push", boom)

    out = await mcp_server.sync_plan_to_watch()

    assert "Nothing was sent" in out, out
    assert "Sent 2" not in out


@pytest.mark.asyncio
async def test_sync_plan_to_watch_bounds_a_long_failure_list(monkeypatch):
    """A 60-week plan failing wholesale must not blow the response budget.

    Plan 1 shipped a defect where a report relied on cap_text for sizing and
    silently dropped its most important sections. Bound the list here instead.
    """
    main, mcp_server, database = await _app(monkeypatch, "sy4")
    await _seed_plan(database, n_sessions=60)

    async def always_broken(w):
        raise RuntimeError("garmin is down " + "x" * 200)
    monkeypatch.setattr(main, "_garmin_push", always_broken)

    out = await mcp_server.sync_plan_to_watch()

    import mcp_views as mv
    cap = mcp_server.MAX_SYNC_FAILURES_LISTED
    assert "truncated at" not in out, "must fit by construction, not by the cap"
    assert len(out) < mv.MAX_RESPONSE_CHARS
    assert "60 failed" in out, "the total count must still be stated"
    assert out.count("Session ") == cap, "list exactly the bounded number"
    assert f"and {60 - cap} more" in out
    # Bounded without cap_text's help: far below the cap, not just under it.
    assert len(out) < mv.MAX_RESPONSE_CHARS // 4, len(out)


# --- push_adhoc_workout ------------------------------------------------------

def _adhoc_steps():
    return [
        {"type": "run", "end_kind": "distance", "end_value": 1000,
         "target_kind": "pace", "pace_low_sec": 470, "pace_high_sec": 490,
         "note": "Do not go faster."},
    ]


def _tomorrow():
    return (datetime.utcnow() + timedelta(days=1)).date().isoformat()


def _spy_adhoc_helper(monkeypatch, mcp_server):
    """Spy on the tool's own helper. The endpoint re-validates date and steps,
    so asserting only on Garmin would pass even with no guard in the tool."""
    calls = []

    async def spy(name, date, steps):
        calls.append((name, date, steps))
        return {"workout_id": 1, "date": date, "name": name, "steps": len(steps)}
    monkeypatch.setattr(mcp_server, "_push_adhoc", spy)
    return calls


@pytest.mark.asyncio
async def test_push_adhoc_workout_returns_the_garmin_id(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ah")
    sent = {}

    async def fake_push(name, steps, date_str):
        sent.update(name=name, steps=steps, date=date_str)
        return {"workout_id": 987}
    monkeypatch.setattr(main.garmin, "push_workout", fake_push)

    tomorrow = _tomorrow()
    out = await mcp_server.push_adhoc_workout(
        name="5K benchmark", date=tomorrow, steps=_adhoc_steps())

    assert "987" in out
    assert tomorrow in out
    assert "5K benchmark" in out
    assert sent["name"] == "5K benchmark"
    assert sent["date"] == tomorrow
    assert sent["steps"] == _adhoc_steps(), "steps must reach Garmin untouched"


@pytest.mark.asyncio
async def test_push_adhoc_workout_refuses_a_past_date(monkeypatch):
    """Scheduling into the past silently does nothing on the watch."""
    main, mcp_server, database = await _app(monkeypatch, "ah2")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)

    yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
    out = await mcp_server.push_adhoc_workout(
        name="Too late", date=yesterday, steps=_adhoc_steps())

    assert calls == [], "the tool must refuse before calling the endpoint"
    assert "past" in out.lower()
    assert "not been sent" in out.lower()


@pytest.mark.asyncio
async def test_push_adhoc_workout_accepts_today(monkeypatch):
    """The guard is for days that have gone, not for today."""
    main, mcp_server, database = await _app(monkeypatch, "ah2b")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)

    today = datetime.utcnow().date().isoformat()
    out = await mcp_server.push_adhoc_workout(
        name="Today", date=today, steps=_adhoc_steps())

    assert len(calls) == 1
    assert "past" not in out.lower()


@pytest.mark.asyncio
async def test_push_adhoc_workout_refuses_an_empty_step_list(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ah3")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)

    out = await mcp_server.push_adhoc_workout(
        name="Empty", date=_tomorrow(), steps=[])

    assert calls == []
    assert "step" in out.lower()
    assert "not been sent" in out.lower()


@pytest.mark.asyncio
async def test_push_adhoc_workout_surfaces_a_garmin_failure(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ah4")

    async def broken(name, steps, date_str):
        raise RuntimeError("no garmin token")
    monkeypatch.setattr(main.garmin, "push_workout", broken)

    out = await mcp_server.push_adhoc_workout(
        name="5K benchmark", date=_tomorrow(), steps=_adhoc_steps())

    assert "no garmin token" in out, "the underlying reason must be shown"
    assert "not been sent" in out.lower(), "the athlete must know the watch has nothing"
    assert "Traceback" not in out and _no_raw_http(out)


@pytest.mark.asyncio
async def test_push_adhoc_workout_rejects_a_malformed_date(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "ah5")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)

    out = await mcp_server.push_adhoc_workout(
        name="x", date="tomorrow", steps=_adhoc_steps())

    assert calls == []
    assert "yyyy-mm-dd" in out.lower()


@pytest.mark.asyncio
async def test_push_adhoc_workout_refuses_swapped_pace_bounds(monkeypatch):
    """pace_low_sec is the FASTER bound (smaller number). Swapped, the watch
    would be given an inverted range and alert on every step."""
    main, mcp_server, database = await _app(monkeypatch, "ah6")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)
    steps = [{"type": "run", "end_kind": "distance", "end_value": 1000,
              "target_kind": "pace", "pace_low_sec": 490, "pace_high_sec": 470}]

    out = await mcp_server.push_adhoc_workout(
        name="x", date=_tomorrow(), steps=steps)

    assert calls == []
    assert "pace_low_sec" in out and "faster" in out.lower()


@pytest.mark.asyncio
async def test_push_adhoc_workout_refuses_a_pace_target_without_bounds(monkeypatch):
    """garmin_workout silently drops the target when a bound is missing, so a
    benchmark would go out with no pace guard at all. Checked inside repeats."""
    main, mcp_server, database = await _app(monkeypatch, "ah7")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)
    steps = [{"type": "repeat", "iterations": 3, "steps": [
        {"type": "run", "end_kind": "time", "end_value": 60,
         "target_kind": "pace", "pace_low_sec": 300}]}]

    out = await mcp_server.push_adhoc_workout(
        name="x", date=_tomorrow(), steps=steps)

    assert calls == []
    assert "pace_high_sec" in out


@pytest.mark.asyncio
async def test_push_adhoc_workout_accepts_the_documented_example(monkeypatch):
    """The docstring's worked example must itself be a valid call."""
    import json
    main, mcp_server, database = await _app(monkeypatch, "ah8")
    calls = _spy_adhoc_helper(monkeypatch, mcp_server)
    doc = mcp_server.push_adhoc_workout.__doc__
    start = doc.index("[\n")
    end = doc.index("\n]\n", start) + len("\n]")
    example = json.loads(doc[start:end])

    out = await mcp_server.push_adhoc_workout(
        name="Intervals", date=_tomorrow(), steps=example)

    assert len(calls) == 1, out
    assert any(s["type"] == "repeat" for s in example)
    # And the real converter must accept it, repeat group included.
    import garmin_workout as gw
    built = gw.build_running_workout("Intervals", example)
    top = built.workoutSegments[0].workoutSteps
    assert len(top) == 3, "warmup, repeat group, cooldown"


# ---------------------------------------------------------------------------
# apply_suggestion
# ---------------------------------------------------------------------------

async def _seed_cap_fixture(database, *, big_gid):
    """Completed 4 km long + MISSED long + oversized upcoming 6.5 km long: the
    shape tests/test_plan_suggestions_endpoint.py uses to get cap_long_run.
    Returns (workout_id, suggestion_id is derived by the caller)."""
    from models import Plan, PlannedWorkout, Activity
    now = datetime.utcnow()
    steps = {"steps": [{"type": "run", "end_kind": "distance",
                        "end_value": 6500, "target_kind": "pace",
                        "pace_low_sec": 435, "pace_high_sec": 450}]}
    async with database.async_session() as s:
        plan = Plan(goal_type="5k", goal_distance_m=5000, target_time_sec=1642,
                    start_date=now - timedelta(days=14),
                    goal_date=now + timedelta(days=42),
                    weeks=8, status="active", created_at=now)
        s.add(plan)
        await s.flush()
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=8),
                             week_number=1, day_type="long",
                             target_distance_m=4000, hr_ceiling=165,
                             title="Long", description="x", structure=steps))
        s.add(PlannedWorkout(plan_id=plan.id, date=now - timedelta(days=3),
                             week_number=2, day_type="long",
                             target_distance_m=5000, hr_ceiling=165,
                             title="Long", description="x"))
        big = PlannedWorkout(plan_id=plan.id, date=now + timedelta(days=4),
                             week_number=3, day_type="long",
                             target_distance_m=6500, hr_ceiling=165,
                             title="Long 6.5k", description="x",
                             structure=steps, garmin_workout_id=big_gid)
        s.add(big)
        s.add(Activity(id=900, name="Run", distance=4000.0,
                       start_date=now - timedelta(days=8),
                       average_speed=1000.0 / 430, average_heartrate=150.0))
        await s.commit()
        return big.id


async def _cap_id(main, database):
    async with database.async_session() as s:
        sugg = (await main.get_plan_suggestions(s))["suggestions"]
    cap = next((x for x in sugg if x["type"] == "cap_long_run"), None)
    assert cap is not None, f"fixture should produce a cap suggestion: {sugg}"
    return cap["id"]


@pytest.mark.asyncio
async def test_apply_suggestion_refuses_a_stale_id(monkeypatch):
    """The server re-derives the list, so an unknown id must be refused."""
    main, mcp_server, database = await _app(monkeypatch, "as")
    await _seed_plan(database)

    out = await mcp_server.apply_suggestion(suggestion_id="not-a-real-id")

    assert "no longer applies" in out.lower(), out
    assert "nothing was changed" in out.lower(), out
    assert _no_raw_http(out) and "Traceback" not in out


@pytest.mark.asyncio
async def test_apply_suggestion_with_no_active_plan_is_a_sentence(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "as2")

    out = await mcp_server.apply_suggestion(suggestion_id="anything")

    assert "no active plan" in out.lower()
    assert _no_raw_http(out)


@pytest.mark.asyncio
async def test_apply_suggestion_tells_him_which_session_came_off_the_watch(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "as3")
    big_id = await _seed_cap_fixture(database, big_gid=7001)
    sid = await _cap_id(main, database)
    pushed, removed = [], []

    async def spy_push(w):
        pushed.append(w.id)

    async def spy_remove(gid):
        removed.append(gid)
    monkeypatch.setattr(main, "_garmin_push", spy_push)
    monkeypatch.setattr(main.garmin, "remove_workout", spy_remove)

    out = await mcp_server.apply_suggestion(suggestion_id=sid)

    assert removed == [7001] and pushed == []
    assert "Cap your next long run at 5.0 km" in out, f"say what was applied: {out}"
    assert "NOT on the watch" in out, out
    assert "Long 6.5k" in out, "name the session that came off, as it was titled"
    assert "workout %d" % big_id in out, out
    assert "do not" in out.lower() and "push_workout" in out, (
        "must warn that pushing it again would resend the stale steps")
    assert "has been updated" not in out and "re-pushed" not in out.lower()
    assert len(out) < 1500, "must not dump the plan response"
    print("\n--- tool output (unscheduled) ---\n" + out)


@pytest.mark.asyncio
async def test_apply_suggestion_says_the_watch_still_holds_the_old_session_when_removal_fails(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "as4")
    big_id = await _seed_cap_fixture(database, big_gid=7002)
    sid = await _cap_id(main, database)

    async def broken(gid):
        raise RuntimeError("garmin 503")
    monkeypatch.setattr(main.garmin, "remove_workout", broken)

    out = await mcp_server.apply_suggestion(suggestion_id=sid)

    assert "plan is updated" in out.lower() or "plan was updated" in out.lower(), out
    assert "still on the watch" in out, out
    assert "old" in out.lower() and "Long 6.5k" in out, out
    assert "unpush_workout" in out, "name the retry"
    assert "garmin 503" in out
    assert "NOT on the watch" not in out, "it IS still on the watch"
    print("\n--- tool output (removal failed) ---\n" + out)


@pytest.mark.asyncio
async def test_apply_suggestion_for_a_session_never_on_the_watch_does_not_mention_the_watch_changing(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "as5")
    await _seed_cap_fixture(database, big_gid=None)
    sid = await _cap_id(main, database)

    out = await mcp_server.apply_suggestion(suggestion_id=sid)

    assert "Cap your next long run at 5.0 km" in out
    assert "came off" not in out and "NOT on the watch" not in out, out
    assert "nothing on the watch needed changing" in out.lower(), out


@pytest.mark.asyncio
async def test_apply_suggestion_reports_repushed_and_bounds_long_lists(monkeypatch):
    """A re-pushed session reads as updated; a long unscheduled list is bounded
    by MAX_SYNC_FAILURES_LISTED, not left to cap_text."""
    main, mcp_server, database = await _app(monkeypatch, "as6")
    await _seed_plan(database)
    n = mcp_server.MAX_SYNC_FAILURES_LISTED + 7
    entries = [{"id": i, "title": f"T{i}", "date": "2026-10-20",
                "was_titled": f"Old{i}"} for i in range(1, n + 1)]

    async def fake(session, suggestion_id):
        return {"watch": {"repushed": [entries[0]], "unscheduled": entries,
                          "failed": []}}
    async def fake_list(session):
        return [{"id": "x", "title": "Do a thing", "detail": "because"}]
    monkeypatch.setattr(mcp_server, "_apply_suggestion", fake)
    monkeypatch.setattr(mcp_server, "_list_suggestions", fake_list)

    out = await mcp_server.apply_suggestion(suggestion_id="x")

    assert "re-sent to the watch" in out.lower() and "T1" in out
    assert out.count("(workout ") <= mcp_server.MAX_SYNC_FAILURES_LISTED + 1
    assert "7 more" in out, out


# ---------------------------------------------------------------------------
# apply_calibration
# ---------------------------------------------------------------------------

# The real shape of padapt.calibrate()["changes"], not a guess: field is
# "easy_pace", from/to are band strings, delta_sec is an int.
_CAL_CHANGE = {
    "field": "easy_pace", "from": "7:15-7:50/km", "to": "6:55-7:30/km",
    "delta_sec": 20,
    "reason": "Your last 4 easy-HR run(s) averaged 7:10/km at or below 155 bpm.",
}


def _cal_response(updated=7, repushed=5, failed=()):
    return {"plan": {"id": 14}, "workouts": [],
            "calibration": {"workouts_updated": updated,
                            "repushed_to_watch": repushed,
                            "changes": [_CAL_CHANGE], "failed": list(failed)}}


@pytest.mark.asyncio
async def test_apply_calibration_with_no_active_plan_is_a_sentence(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "cal")

    out = await mcp_server.apply_calibration()

    assert "no active plan" in out.lower()
    assert "nothing was changed" in out.lower()
    assert _no_raw_http(out) and "Traceback" not in out


@pytest.mark.asyncio
async def test_apply_calibration_says_so_when_there_is_nothing_to_change(monkeypatch):
    """A fresh plan with no easy runs behind it cannot be calibrated yet.

    The endpoint raises 400 with that reason; it must read as 'nothing to do',
    not as a failure and not as a success.
    """
    main, mcp_server, database = await _app(monkeypatch, "cal2")
    await _seed_plan(database)

    out = await mcp_server.apply_calibration()

    low = out.lower()
    assert "nothing to calibrate" in low, out
    assert "nothing was changed" in low, out
    assert "applied" not in low.replace("not applied", ""), out
    assert _no_raw_http(out) and "Traceback" not in out


@pytest.mark.asyncio
async def test_apply_calibration_clean_run_reports_counts_and_no_watch_alarm(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "cal3")
    await _seed_plan(database)

    async def fake(session):
        return _cal_response(updated=7, repushed=7)
    monkeypatch.setattr(mcp_server, "_apply_calibration", fake)

    out = await mcp_server.apply_calibration()

    assert "7:15-7:50/km -> 6:55-7:30/km" in out
    assert "7 upcoming session" in out
    assert "7 of them" in out and "re-sent" in out
    low = out.lower()
    assert "old" not in low and "did not reach" not in low and "not been sent" not in low, out


@pytest.mark.asyncio
async def test_apply_calibration_names_sessions_the_watch_did_not_take(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "cal4")
    await _seed_plan(database)

    async def fake(session):
        return _cal_response(updated=7, repushed=5, failed=[
            {"id": 3, "title": "Long run"}, {"id": 9, "title": "Easy 5k"}])
    monkeypatch.setattr(mcp_server, "_apply_calibration", fake)

    out = await mcp_server.apply_calibration()

    low = out.lower()
    assert "saved" in low, "the plan did change"
    assert "2 session" in low and "did not reach the watch" in low, out
    assert "Long run" in out and "workout 3" in out
    assert "Easy 5k" in out and "workout 9" in out
    assert "old" in low, "must say the watch still holds the old targets"
    assert "5 of them" in out, "5 were re-sent, not 7"
    assert "push_workout" in out, "name the retry"


@pytest.mark.asyncio
async def test_apply_calibration_bounds_a_long_failed_list(monkeypatch):
    main, mcp_server, database = await _app(monkeypatch, "cal5")
    await _seed_plan(database)
    n = mcp_server.MAX_SYNC_FAILURES_LISTED + 6
    failed = [{"id": i, "title": f"T{i}"} for i in range(1, n + 1)]

    async def fake(session):
        return _cal_response(updated=n, repushed=0, failed=failed)
    monkeypatch.setattr(mcp_server, "_apply_calibration", fake)

    out = await mcp_server.apply_calibration()

    assert out.count("(workout ") == mcp_server.MAX_SYNC_FAILURES_LISTED
    assert "6 more" in out, out
    assert f"{n} session" in out, "the headline count is the true total"


@pytest.mark.asyncio
async def test_apply_calibration_end_to_end_with_a_garmin_failure(monkeypatch):
    """Through the real endpoint: one re-push is rejected by Garmin.

    Only the calibration maths (_calibration) and the Garmin call are stubbed.
    """
    main, mcp_server, database = await _app(monkeypatch, "cal6")
    await _seed_plan(database, n_sessions=3, on_watch=True)
    pushed = []

    async def fake_calibration(session, plan):
        return {"has_changes": True, "proposed": {"easy_low_sec": 400},
                "changes": [_CAL_CHANGE], "insights": []}

    async def fake_push(w):
        if w.id == 2:
            raise RuntimeError("garmin said no")
        pushed.append(w.id)
    monkeypatch.setattr(main, "_calibration", fake_calibration)
    monkeypatch.setattr(main, "_garmin_push", fake_push)

    out = await mcp_server.apply_calibration()

    assert sorted(pushed) == [1, 3], "all three were attempted; 2 raised"
    assert "Session 2" in out and "workout 2" in out, out
    assert "did not reach the watch" in out
    assert "2 of them" in out, "3 re-aimed, 1 failed, 2 re-sent"
    assert _no_raw_http(out) and "Traceback" not in out
