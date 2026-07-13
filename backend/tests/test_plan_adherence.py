from datetime import datetime, timedelta
import plan_adherence as pa

NOW = datetime(2026, 7, 20, 12, 0, 0)


def _wo(wid, days_ago, day_type, km, hr_ceiling=160, week=1):
    return {"id": wid, "date": NOW - timedelta(days=days_ago), "week_number": week,
            "day_type": day_type, "target_distance_m": km * 1000,
            "pace_low_sec": 420, "pace_high_sec": 455, "hr_ceiling": hr_ceiling,
            "title": "x", "description": "y"}


def _act(aid, days_ago, km, pace_sec, hr):
    return {"id": aid, "distance": km * 1000, "start_date": NOW - timedelta(days=days_ago),
            "average_speed": 1000.0 / pace_sec, "average_heartrate": hr}


def test_done_missed_upcoming():
    workouts = [
        _wo(1, 4, "easy", 3.0),         # past — will match
        _wo(2, 2, "easy", 3.0),         # past — no run -> missed
        _wo(3, -3, "long", 5.0),        # future -> upcoming
    ]
    acts = [_act(100, 4, 3.0, 430, 150)]  # matches workout 1, easy on-target
    r = pa.match_and_grade(workouts, acts, NOW)
    by_id = {w["id"]: w for w in r["workouts"]}
    assert by_id[1]["status"] == "done" and by_id[1]["compliance"] == "on_target"
    assert by_id[2]["status"] == "missed"
    assert by_id[3]["status"] == "upcoming"
    assert r["summary"]["done"] == 1 and r["summary"]["missed"] == 1


def test_easy_run_hard_flagged():
    workouts = [_wo(1, 3, "easy", 3.0, hr_ceiling=160)]
    acts = [_act(100, 3, 3.0, 360, 185)]  # HR 185 >> ceiling 160 -> ran hard
    r = pa.match_and_grade(workouts, acts, NOW)
    assert r["workouts"][0]["compliance"] == "ran_hard"
    assert r["summary"]["easy_run_hard"] == 1


def test_suggest_cap_long_after_missed():
    workouts = [
        _wo(1, 6, "long", 4.0),    # completed long (4km)
        _wo(2, 3, "long", 5.0),    # missed long
        _wo(3, -4, "long", 5.0),   # upcoming long at 5km
    ]
    acts = [_act(100, 6, 4.0, 430, 150)]  # only the first long is done
    # upcoming long at 5.0 == cap (4.0 + 1) -> no cap needed; bump to 6.5 to force it
    workouts[2]["target_distance_m"] = 6500
    r2 = pa.match_and_grade(workouts, acts, NOW)
    sugg2 = pa.suggest(r2["workouts"], r2["summary"], NOW)
    cap = next(s for s in sugg2 if s["type"] == "cap_long_run")
    assert cap["changes"][0]["value"] == 5000  # capped to 5.0 km


def test_suggest_soften_quality_after_hard_easy():
    workouts = [
        _wo(1, 6, "easy", 3.0), _wo(2, 4, "easy", 3.0),   # two easy days
        _wo(3, -2, "quality", 4.0),                        # upcoming speed session
    ]
    acts = [_act(100, 6, 3.0, 360, 188), _act(101, 4, 3.0, 360, 190)]  # both run hard
    r = pa.match_and_grade(workouts, acts, NOW)
    sugg = pa.suggest(r["workouts"], r["summary"], NOW)
    soft = [s for s in sugg if s["type"] == "soften_quality"]
    assert soft
    assert any(c["field"] == "day_type" and c["value"] == "easy" for c in soft[0]["changes"])
