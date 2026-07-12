import json, pathlib, tempfile
import pytest

FIX = pathlib.Path(__file__).parent / "fixtures"

@pytest.mark.asyncio
async def test_persist_garmin_activity(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Split, Stream
    import main
    importlib.reload(main)

    summary = json.loads((FIX / "garmin_summary.json").read_text())
    splits = json.loads((FIX / "garmin_splits.json").read_text())
    details = json.loads((FIX / "garmin_details.json").read_text())
    zones = json.loads((FIX / "garmin_hr_zones.json").read_text())

    async with database.async_session() as s:
        await main._persist_garmin_activity(s, summary, splits, details, zones)
        await s.commit()
        act = await s.get(Activity, summary["activityId"])
        assert act is not None
        assert act.source == "garmin"
        assert act.has_detailed_data is True
        assert act.map_summary_polyline  # derived from latlng
