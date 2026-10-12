"""Report composers: rows and streams in, formatted text out.

These take already-fetched data and return strings, so they are testable with
no database and no network — the same contract mcp_views.py holds.
"""
from datetime import datetime

import mcp_reports as mr
import mcp_views as mv


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


def test_run_review_keeps_every_section_on_a_very_long_run():
    """The cap used to eat the judgment and keep the filler.

    A 42 km run at 10 s windows generated 1,440 near-identical HR rows and
    57,252 characters, so cardiac drift, the zone shares, the athlete's note
    and the entire planned-vs-actual section fell past the 25,000-character
    cap — and the output still read as a finished report.
    """
    n = 14400
    run = {
        "id": 1, "name": "Long", "distance": 42000.0, "moving_time": n,
        "elapsed_time": n + 5, "average_heartrate": 150.0, "max_heartrate": 170.0,
        "notes": "felt strong throughout", "temp_c": 25.0, "dew_point_c": 24.0,
        "total_elevation_gain": 10.0, "elev_high": 100.0, "elev_low": 95.0,
        "hr_zones": None, "start_latlng": None, "end_latlng": None,
        "start_date": None,
    }
    streams = {
        "time": list(range(n)),
        "distance": [i * 42000 / n for i in range(n)],
        "heartrate": [150] * n,
        "altitude": [100.0] * n,
    }
    planned = {
        "title": "Long run", "day_type": "long", "target_distance_m": 42000,
        "pace_low_sec": 340, "pace_high_sec": 360, "hr_ceiling": 160,
        "status": "done", "compliance": "on_target",
    }

    out = mr.run_review(run, streams, planned=planned, window_sec=10)

    assert "truncated at" not in out, "the report must fit by construction"
    assert len(out) < mv.MAX_RESPONSE_CHARS
    assert "Cardiac drift" in out
    assert "felt strong throughout" in out, "the athlete's note was being dropped"
    assert "Long run" in out, "the planned-vs-actual section was being dropped"
    assert "widened from 10 s" in out, "say so rather than silently coarsening"


def test_run_review_does_not_widen_a_normal_run():
    """46 minutes at 60 s windows must keep its minute-by-minute resolution.

    The whole value of the HR profile for this athlete is seeing HR sit at 160
    for the first five minutes and drift to 188 by the end, so the row budget
    must not coarsen a run he actually does.
    """
    n = 2775
    run = {
        "id": 2, "name": "5K", "distance": 5968.0, "moving_time": n,
        "elapsed_time": n + 2, "average_heartrate": 188.0, "max_heartrate": 194.0,
        "hr_zones": None, "start_latlng": None, "end_latlng": None,
        "start_date": None, "total_elevation_gain": 0.0,
    }
    streams = {
        "time": list(range(n)),
        "distance": [i * 5968 / n for i in range(n)],
        "heartrate": [160 + (i * 28) // n for i in range(n)],
    }

    out = mr.run_review(run, streams, window_sec=60)

    assert "widened" not in out, "a 46-minute run must keep 60 s windows"
    assert "Splits (500 m" in out, "and 500 m splits"
