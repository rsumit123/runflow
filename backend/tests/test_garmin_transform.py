import json, pathlib
from datetime import datetime
import garmin_transform as gt

FIX = pathlib.Path(__file__).parent / "fixtures"
summary = json.loads((FIX / "garmin_summary.json").read_text())
splits = json.loads((FIX / "garmin_splits.json").read_text())
details = json.loads((FIX / "garmin_details.json").read_text())
zones = json.loads((FIX / "garmin_hr_zones.json").read_text())

def test_is_garmin_running():
    assert gt.is_garmin_running("track_running")
    assert gt.is_garmin_running("running")
    assert not gt.is_garmin_running("cycling")
    assert not gt.is_garmin_running(None)

def test_summary_fields():
    f = gt.summary_to_activity_fields(summary)
    assert f["id"] == summary["activityId"]
    assert f["source"] == "garmin"
    assert f["distance"] == summary["distance"]
    assert isinstance(f["start_date"], datetime)
    assert f["sport_type"] == summary["activityType"]["typeKey"]
    assert f["average_heartrate"] == summary["averageHR"]

def test_splits_nonempty():
    rows = gt.laps_to_splits(splits)
    assert len(rows) >= 1
    assert rows[0]["split_number"] == 1
    assert "average_speed" in rows[0]

def test_streams_have_core_types():
    s = gt.details_to_streams(details)
    assert "latlng" in s and len(s["latlng"]) > 10
    assert "time" in s and s["time"][0] == 0          # elapsed starts at 0
    assert len(s["latlng"]) == len(s["time"])         # index-aligned
    assert isinstance(s["latlng"][0], list) and len(s["latlng"][0]) == 2
    assert "heartrate" in s                            # HR present in this run

def test_hr_zones_normalized():
    z = gt.hr_zones(zones)
    assert isinstance(z, list)
    assert z and "zone" in z[0] and "secs" in z[0]
