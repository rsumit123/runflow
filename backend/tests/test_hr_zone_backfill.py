"""The backfill that puts every stored run on one set of zone boundaries."""
import tempfile
from datetime import datetime, timedelta
import pytest


@pytest.mark.asyncio
async def test_backfill_restates_zones_against_observed_max(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Stream
    import main
    importlib.reload(main)

    now = datetime(2026, 8, 30, 6, 0, 0)
    async with database.async_session() as s:
        # An old run Garmin filed against a stale max of 190: it called 175 bpm "Z5".
        s.add(Activity(id=1, name="July run", distance=3000.0,
                       start_date=now - timedelta(days=50),
                       average_speed=1000.0 / 400, average_heartrate=175.0,
                       max_heartrate=190.0,
                       hr_zones=[{"zone": 5, "secs": 300.0, "low_bpm": 171}]))
        s.add(Stream(activity_id=1, stream_type="heartrate", data=[175.0] * 300))
        s.add(Stream(activity_id=1, stream_type="time", data=list(range(300))))
        # A recent run that establishes the athlete's real max of 207.
        s.add(Activity(id=2, name="August run", distance=3000.0,
                       start_date=now - timedelta(days=1),
                       average_speed=1000.0 / 420, average_heartrate=187.0,
                       max_heartrate=207.0))
        await s.commit()

        result = await main.backfill_hr_zones(s)
        assert result["updated"] == 1
        assert result["max_hr"] == 207

        row = await s.get(Activity, 1)
        # Against a max of 207, 175 bpm is Zone 4 — not the Zone 5 Garmin stored.
        secs = {z["zone"]: z["secs"] for z in row.hr_zones}
        assert secs[4] == 300.0
        assert secs[5] == 0.0
        assert [z["low_bpm"] for z in row.hr_zones] == [104, 124, 145, 166, 186]


@pytest.mark.asyncio
async def test_backfill_leaves_runs_without_hr_streams_alone(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity
    import main
    importlib.reload(main)

    stored = [{"zone": 5, "secs": 120.0, "low_bpm": 171}]
    async with database.async_session() as s:
        s.add(Activity(id=1, name="No streams", distance=3000.0,
                       start_date=datetime(2026, 8, 29, 6, 0, 0),
                       average_speed=1000.0 / 400, average_heartrate=180.0,
                       max_heartrate=207.0, hr_zones=stored))
        await s.commit()

        result = await main.backfill_hr_zones(s)
        assert result["updated"] == 0
        row = await s.get(Activity, 1)
        assert row.hr_zones == stored  # Garmin's payload is the fallback, not discarded


@pytest.mark.asyncio
async def test_import_stores_zones_on_athlete_boundaries_not_garmins(monkeypatch):
    """A freshly imported run must not land on Garmin's drifted boundaries.

    The fixture is a real run with maxHR 207, but Garmin filed its zones against
    a stale estimate of 201 (Z5 floor 181). Stored as-is, it would be
    incomparable with every run imported after Garmin caught up.
    """
    import json, pathlib
    fix = pathlib.Path(__file__).parent / "fixtures"
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity
    import main
    importlib.reload(main)

    summary = json.loads((fix / "garmin_summary.json").read_text())
    splits = json.loads((fix / "garmin_splits.json").read_text())
    details = json.loads((fix / "garmin_details.json").read_text())
    zones = json.loads((fix / "garmin_hr_zones.json").read_text())

    async with database.async_session() as s:
        await main._persist_garmin_activity(s, summary, splits, details, zones)
        await s.commit()

        act = await s.get(Activity, summary["activityId"])
        assert [z["low_bpm"] for z in act.hr_zones] == [104, 124, 145, 166, 186]
