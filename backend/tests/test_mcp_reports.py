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
    # An average HR that differs from the stream's 180, so it can only come
    # from the run row and only from the headline.
    out = mr.run_review(_run_row(average_heartrate=173.0), _steady_run())

    assert "3.0 km" in out
    assert "7:15" in out, "1305 s over 3 km is 7:15/km"
    headline = out.split("\n")[1]
    assert "3.0 km" in headline and "7:15/km" in headline
    assert "avg HR 173" in headline


def test_run_review_includes_splits_and_an_hr_profile():
    out = mr.run_review(_run_row(), _steady_run(), window_sec=300)

    assert "splits" in out.lower()
    assert "hr profile" in out.lower()
    # 500 m splits over 3 km is six rows.
    assert out.lower().count("0-500") == 1
    lines = out.split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("Splits (")) + 2
    end = next(i for i, l in enumerate(lines) if l.startswith("HR profile"))
    rows = [l for l in lines[start:end] if l.strip()]
    assert len(rows) == 6, f"expected six 500 m splits, got {len(rows)}: {rows}"


def test_run_review_flags_a_continuous_run_as_continuous():
    out = mr.run_review(_run_row(), _steady_run())

    assert "continuous" in out.lower()
    assert "FRAGMENTED" not in out


def test_run_review_reports_pauses_when_the_run_was_fragmented():
    streams = _steady_run(metres=3000)
    # Insert a 600 s standing pause at the 1500 m mark.
    for i in range(1500, len(streams["time"])):
        streams["time"][i] += 600
    row = _run_row(elapsed_time=1907)

    out = mr.run_review(row, streams)

    assert "pause" in out.lower()
    detail = [l for l in out.split("\n") if l.strip().startswith("at ")]
    assert any("1.5 km" in l and "10m00s" in l for l in detail), detail


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


def test_run_review_fits_when_the_row_has_no_moving_time():
    """moving_time is nullable, and keying the row budget off it alone truncated.

    A 42 km stream with moving_time=0 produced 25,042 characters and dropped
    cardiac drift, the note and the planned section — the same failure the row
    budget was added to prevent.
    """
    n = 14400
    run = {
        "id": 1, "name": "Long", "distance": 42000.0, "moving_time": 0,
        "elapsed_time": 0, "average_heartrate": 150.0, "max_heartrate": 170.0,
        "notes": "no moving time on this row", "hr_zones": None,
        "start_latlng": None, "end_latlng": None, "start_date": None,
        "total_elevation_gain": 10.0, "elev_high": 100.0, "elev_low": 95.0,
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

    assert "truncated at" not in out
    assert "Cardiac drift" in out
    assert "no moving time on this row" in out
    assert "Long run" in out


def test_run_review_caps_the_pause_list():
    """299 pause lines is a wall nobody reads, even though it fits."""
    time, dist, t, d = [], [], 0, 0.0
    for _ in range(300):
        for _ in range(5):
            time.append(t); dist.append(d); t += 1; d += 2.5
        time.append(t); dist.append(d); t += 30

    run = {
        "id": 1, "name": "Stop-start", "distance": d, "moving_time": 1500,
        "elapsed_time": t, "average_heartrate": 150.0, "max_heartrate": 170.0,
        "hr_zones": None, "start_latlng": None, "end_latlng": None,
        "start_date": None, "total_elevation_gain": 0.0,
    }

    out = mr.run_review(run, {"time": time, "distance": dist,
                              "heartrate": [150] * len(time)}, window_sec=60)

    assert "FRAGMENTED" in out
    detail = [l for l in out.split("\n") if l.strip().startswith("at ")]
    assert len(detail) == mr.MAX_PAUSE_LINES, (
        f"expected {mr.MAX_PAUSE_LINES} detail lines, got {len(detail)}"
    )
    assert "and 279 more" in out


def test_plan_status_shows_the_week_todays_session_and_suggestions():
    plan = {"id": 14, "goal_type": "5k", "target_time_sec": 1800, "weeks": 12,
            "start_date": datetime(2026, 10, 19), "goal_date": datetime(2027, 1, 9)}
    workouts = [
        {"id": 1, "date": datetime(2026, 10, 19), "week_number": 1, "day_type": "easy",
         "title": "Easy run", "target_distance_m": 3000.0, "pace_low_sec": 435,
         "pace_high_sec": 450, "hr_ceiling": 160, "garmin_workout_id": 555,
         "status": "done", "compliance": "ran_hard",
         "actual": {"activity_id": 99, "pace_sec": 435, "avg_hr": 186.0,
                    "distance_m": 3010.0}},
        {"id": 2, "date": datetime(2026, 10, 22), "week_number": 1, "day_type": "easy",
         "title": "Easy run", "target_distance_m": 3500.0, "pace_low_sec": 435,
         "pace_high_sec": 450, "hr_ceiling": 160, "garmin_workout_id": None,
         "status": "upcoming", "compliance": None, "actual": None},
    ]
    # Real keys from plan_adherence.match_and_grade's summary.
    summary = {"done": 1, "missed": 0, "planned_past": 1, "easy_run_hard": 1,
               "easy_graded": 1, "adherence_pct": 100}
    guidance = {"recommendation": {"action": "proceed", "reason": "readiness 61"}}
    # Real keys from plan_adherence.suggest: id, type, title, detail, changes.
    suggestions = [{"id": "cap_long_2", "type": "cap_long_run",
                    "title": "Cap your next long run at 5.0 km",
                    "detail": "You missed a long run."}]

    out = mr.plan_status(plan, workouts, summary, guidance, suggestions,
                         today=datetime(2026, 10, 22))

    assert "30:00" in out, "the target has to be legible, not raw seconds"
    assert "2027-01-09" in out
    assert "Easy run" in out
    assert "on watch" in out.lower()
    assert "proceed" in out.lower()
    assert "ran_hard" in out, "an easy run run hard is the thing worth seeing"
    assert "cap_long_2" in out, "a suggestion id is needed to apply it later"


def test_plan_status_says_so_with_no_active_plan():
    out = mr.plan_status(None, [], {}, None, [], today=datetime(2026, 10, 22))

    assert "no active plan" in out.lower()
