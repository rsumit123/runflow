"""Report composers: rows and streams in, formatted text out.

These take already-fetched data and return strings, so they are testable with
no database and no network — the same contract mcp_views.py holds.
"""
from datetime import datetime

import mcp_reports as mr


def _steady_run(metres=3000, pace_sec_per_km=435, hr=180.0):
    """A clean continuous run at one pace, for hand-computable assertions."""
    time, dist, hrs = [], [], []
    for m in range(0, metres + 1):
        time.append(round(m * pace_sec_per_km / 1000.0))
        dist.append(float(m))
        hrs.append(hr)
    return {"time": time, "distance": dist, "heartrate": hrs}


def _run_row(**over):
    row = {
        "id": 1, "name": "Jamshedpur Running", "start_date": datetime(2026, 10, 12, 1, 45),
        "distance": 3000.0, "moving_time": 1305, "elapsed_time": 1307,
        "average_heartrate": 180.0, "max_heartrate": 194.0, "average_cadence": 152.0,
        "total_elevation_gain": 0.0, "elev_high": 174.4, "elev_low": 172.6,
        "hr_zones": [{"zone": 1, "secs": 10.0, "low_bpm": 105},
                     {"zone": 2, "secs": 20.0, "low_bpm": 126},
                     {"zone": 3, "secs": 100.0, "low_bpm": 147},
                     {"zone": 4, "secs": 1100.0, "low_bpm": 168},
                     {"zone": 5, "secs": 75.0, "low_bpm": 189}],
        "temp_c": 28.0, "dew_point_c": 22.8, "notes": None,
    }
    row.update(over)
    return row


def test_run_review_reports_distance_pace_and_hr():
    out = mr.run_review(_run_row(), _steady_run())

    assert "3.0 km" in out
    assert "7:15" in out, "1305 s over 3 km is 7:15/km"
    assert "180" in out


def test_run_review_includes_splits_and_an_hr_profile():
    out = mr.run_review(_run_row(), _steady_run(), window_sec=300)

    assert "splits" in out.lower()
    assert "hr profile" in out.lower()
    # 500 m splits over 3 km is six rows.
    assert out.lower().count("0-500") == 1


def test_run_review_flags_a_continuous_run_as_continuous():
    out = mr.run_review(_run_row(), _steady_run())

    assert "continuous" in out.lower()
    assert "pause" not in out.lower().split("continuous")[0]


def test_run_review_reports_pauses_when_the_run_was_fragmented():
    streams = _steady_run(metres=3000)
    # Insert a 600 s standing pause at the 1500 m mark.
    for i in range(1500, len(streams["time"])):
        streams["time"][i] += 600
    row = _run_row(elapsed_time=1907)

    out = mr.run_review(row, streams)

    assert "pause" in out.lower()
    assert "1.5" in out


def test_run_review_shows_the_terrain_and_the_athletes_note():
    out = mr.run_review(_run_row(notes="Flat stadium track."), _steady_run())

    assert "flat" in out.lower()
    assert "Flat stadium track." in out


def test_run_review_compares_against_the_planned_session_when_there_was_one():
    # Shape produced by plan_adherence.match_and_grade: the planned workout
    # dict plus `status` (rest|done|missed|upcoming), `actual`, and for easy
    # runs `compliance` (on_target | hr_drift | ran_hard).
    planned = {
        "id": 42, "day_type": "easy", "title": "Easy run",
        "target_distance_m": 3000.0, "pace_low_sec": 435, "pace_high_sec": 450,
        "hr_ceiling": 160, "status": "done", "compliance": "ran_hard",
        "actual": {"activity_id": 1, "pace_sec": 435, "avg_hr": 180.0,
                   "distance_m": 3000.0},
    }

    out = mr.run_review(_run_row(), _steady_run(), planned=planned)

    assert "planned" in out.lower()
    assert "Easy run" in out
    assert "160" in out, "the HR ceiling that was missed has to appear"
    assert "ran_hard" in out


def test_run_review_says_so_when_there_was_no_planned_session():
    out = mr.run_review(_run_row(), _steady_run(), planned=None)

    assert "no planned session" in out.lower()


def test_run_review_survives_a_run_with_no_streams_at_all():
    out = mr.run_review(_run_row(), {})

    assert "3.0 km" in out
    assert "no stream" in out.lower()


def test_run_review_never_exceeds_the_response_cap():
    import mcp_views as mv
    out = mr.run_review(_run_row(distance=42000.0, moving_time=18000),
                        _steady_run(metres=42000), window_sec=10)

    assert len(out) <= mv.MAX_RESPONSE_CHARS + 120
