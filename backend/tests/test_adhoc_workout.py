"""One-off workouts pushed to the watch without a plan behind them.

A benchmark attempt — "run 5 km, first kilometre at 8:00/km" — is not part of a
training plan, but it is exactly the kind of run that needs the watch to hold
the pace, because the athlete's failure mode is starting too fast.
"""
import tempfile
from datetime import date, timedelta

import pytest


async def _app(monkeypatch, tag):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)
    return main


def _steps():
    return [
        {"type": "run", "end_kind": "distance", "end_value": 1000,
         "target_kind": "pace", "pace_low_sec": 470, "pace_high_sec": 490,
         "note": "Do not go faster."},
    ]


@pytest.mark.asyncio
async def test_pushes_an_adhoc_workout_and_returns_the_garmin_id(monkeypatch):
    main = await _app(monkeypatch, "aa")
    sent = {}

    async def fake_push(name, steps, date_str):
        sent.update({"name": name, "steps": steps, "date": date_str})
        return {"workout_id": 987}
    monkeypatch.setattr(main.garmin, "push_workout", fake_push)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    res = await main.push_adhoc_workout(main.AdhocWorkoutRequest(
        name="5K test", date=tomorrow, steps=_steps()
    ))

    assert res["workout_id"] == 987
    assert res["date"] == tomorrow
    assert sent["date"] == tomorrow
    assert sent["steps"][0]["pace_low_sec"] == 470


@pytest.mark.asyncio
async def test_refuses_a_workout_with_no_steps(monkeypatch):
    main = await _app(monkeypatch, "ab")
    from fastapi import HTTPException

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException):
        await main.push_adhoc_workout(main.AdhocWorkoutRequest(
            name="Empty", date=tomorrow, steps=[]
        ))


@pytest.mark.asyncio
async def test_refuses_a_date_in_the_past(monkeypatch):
    """Scheduling into the past silently does nothing on the watch."""
    main = await _app(monkeypatch, "ac")
    from fastapi import HTTPException

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException):
        await main.push_adhoc_workout(main.AdhocWorkoutRequest(
            name="Past", date=yesterday, steps=_steps()
        ))


@pytest.mark.asyncio
async def test_surfaces_a_garmin_failure_as_502(monkeypatch):
    main = await _app(monkeypatch, "ad")
    from fastapi import HTTPException

    async def broken(name, steps, date_str):
        raise RuntimeError("no garmin token")
    monkeypatch.setattr(main.garmin, "push_workout", broken)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException) as exc:
        await main.push_adhoc_workout(main.AdhocWorkoutRequest(
            name="5K test", date=tomorrow, steps=_steps()
        ))
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_removing_an_adhoc_workout_calls_garmin(monkeypatch):
    main = await _app(monkeypatch, "ae")
    removed = []

    async def fake_remove(workout_id):
        removed.append(workout_id)
    monkeypatch.setattr(main.garmin, "remove_workout", fake_remove)

    res = await main.delete_adhoc_workout(987)

    assert removed == [987]
    assert res["removed"] == 987
