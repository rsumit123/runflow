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


def test_preplan_run_not_matched():
    # workout on plan day 0 (NOW-... let plan start = NOW-3d); a run BEFORE start
    # must not fulfil it.
    plan_start = NOW - timedelta(days=3)
    workouts = [_wo(1, 3, "easy", 3.0)]           # dated at plan start
    acts = [_act(100, 4, 3.0, 430, 150)]          # a run one day BEFORE plan start
    r = pa.match_and_grade(workouts, acts, NOW, plan_start=plan_start)
    assert r["workouts"][0]["status"] == "missed"  # not matched to the pre-plan run
    # without the plan_start guard it WOULD match (±1 day)
    r2 = pa.match_and_grade(workouts, acts, NOW)
    assert r2["workouts"][0]["status"] == "done"


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


# --- compliance distinguishes 'ran hard' from heat-driven HR drift -----------

def test_paced_right_but_hr_high_is_hr_drift_not_ran_hard():
    # Today's real run: 7:32/km (band 7:00-7:35), avg HR 173, ceiling 160.
    wo = [_wo(1, 0, "easy", 2.5)]                       # band 420-455 (7:00-7:35)
    act = [_act(100, 0, 3.0, 452, 173)]                # paced right, HR over
    r = pa.match_and_grade(wo, act, NOW)
    assert r["workouts"][0]["compliance"] == "hr_drift"


def test_running_faster_than_the_band_with_high_hr_is_ran_hard():
    wo = [_wo(1, 0, "easy", 2.5)]
    act = [_act(100, 0, 3.0, 400, 173)]                # 6:40/km — faster than 7:35 band, HR high
    r = pa.match_and_grade(wo, act, NOW)
    assert r["workouts"][0]["compliance"] == "ran_hard"


def test_hr_under_ceiling_is_always_on_target():
    wo = [_wo(1, 0, "easy", 2.5)]
    act = [_act(100, 0, 3.0, 452, 150)]
    r = pa.match_and_grade(wo, act, NOW)
    assert r["workouts"][0]["compliance"] == "on_target"


def test_hr_drift_does_not_count_against_the_ran_hard_tally():
    wo = [_wo(1, 0, "easy", 2.5)]
    act = [_act(100, 0, 3.0, 452, 173)]
    r = pa.match_and_grade(wo, act, NOW)
    # It's still graded (an easy day we assessed) but it is NOT a ran-hard.
    assert r.get("easy_run_hard", 0) == 0
