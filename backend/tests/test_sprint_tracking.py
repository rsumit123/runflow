from datetime import datetime, timedelta
import sprint_tracking as st

NOW = datetime(2026, 7, 20, 12, 0, 0)


def _wo(wid, days_ago, day_type, week=1, title="x"):
    return {"id": wid, "date": NOW - timedelta(days=days_ago), "week_number": week,
            "day_type": day_type, "title": title}


def _act(aid, days_ago, best_100m=None, fade=None, fastest=None):
    return {"id": aid, "start_date": NOW - timedelta(days=days_ago),
            "best_100m_sec": best_100m, "fade_pct": fade, "fastest_rep_sec": fastest}


def test_done_match_captures_actual():
    workouts = [_wo(1, 4, "max_velocity")]
    acts = [_act(100, 3, best_100m=20.5, fade=8.0, fastest=12.1)]  # within 1 day
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    w = r["workouts"][0]
    assert w["status"] == "done"
    assert w["actual"] == {
        "activity_id": 100, "best_100m_sec": 20.5, "fade_pct": 8.0, "fastest_rep_sec": 12.1,
    }


def test_missed_past_workout():
    workouts = [_wo(1, 3, "speed_endurance")]
    acts = []  # nothing to match
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    w = r["workouts"][0]
    assert w["status"] == "missed"
    assert w["actual"] is None


def test_upcoming_future_workout():
    workouts = [_wo(1, -3, "accel")]  # 3 days in the future
    acts = []
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    w = r["workouts"][0]
    assert w["status"] == "upcoming"
    assert w["actual"] is None


def test_rest_status():
    workouts = [_wo(1, 3, "rest")]
    acts = [_act(100, 3, best_100m=20.0)]
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    w = r["workouts"][0]
    assert w["status"] == "rest"
    assert w["actual"] is None


def test_plyometrics_never_matched():
    # plyometrics is non-matchable; past -> missed, actual None, activity untouched
    workouts = [_wo(1, 3, "plyometrics"), _wo(2, 3, "accel")]
    acts = [_act(100, 3, best_100m=21.0)]  # should go to the accel workout, not plyo
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    by_id = {w["id"]: w for w in r["workouts"]}
    assert by_id[1]["status"] == "missed"
    assert by_id[1]["actual"] is None
    assert by_id[2]["status"] == "done"
    assert by_id[2]["actual"]["activity_id"] == 100


def test_preplan_activity_not_matched():
    plan_start = NOW - timedelta(days=3)
    workouts = [_wo(1, 3, "max_velocity")]  # dated at plan start
    acts = [_act(100, 4, best_100m=19.0)]   # one day BEFORE plan start
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=plan_start)
    w = r["workouts"][0]
    assert w["status"] == "missed"
    assert w["actual"] is None


def test_activity_not_double_claimed():
    # two matchable workouts on the same day, only one activity -> one done, one missed
    workouts = [_wo(1, 3, "accel"), _wo(2, 3, "max_velocity")]
    acts = [_act(100, 3, best_100m=20.0)]
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    statuses = sorted(w["status"] for w in r["workouts"])
    assert statuses == ["done", "missed"]


def test_out_of_window_not_matched():
    workouts = [_wo(1, 6, "accel")]
    acts = [_act(100, 3, best_100m=20.0)]  # 3 days off -> outside ±2 window
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    assert r["workouts"][0]["status"] == "missed"


def test_progress_dict():
    workouts = [
        _wo(1, 6, "accel"),            # matched -> done
        _wo(2, 4, "max_velocity"),     # matched -> done
        _wo(3, 2, "speed_endurance"),  # no activity -> missed
        _wo(4, -3, "technique"),       # future -> upcoming
        _wo(5, 3, "rest"),             # rest
        _wo(6, 3, "plyometrics"),      # non-matchable, past -> missed (not counted)
    ]
    acts = [
        _act(100, 6, best_100m=21.0, fade=9.0, fastest=13.0),
        _act(101, 4, best_100m=20.0, fade=8.0, fastest=12.5),  # most recent matched
    ]
    r = st.match_sprint_sessions(workouts, acts, NOW, plan_start=NOW - timedelta(days=10))
    p = r["progress"]
    assert p["sessions_done"] == 2
    # planned_past counts matchable (5 types) that are past: done+missed = 3 (accel, mv, se)
    assert p["sessions_planned_past"] == 3
    assert p["adherence_pct"] == round(100 * 2 / 3)
    # latest by activity start_date is act 101 (4 days ago) -> 20.0
    assert p["latest_best_100m_sec"] == 20.0
    # trend sorted ascending by date: act 100 (older) then act 101
    assert p["best_100m_trend"] == [
        {"date": (NOW - timedelta(days=6)).date().isoformat(), "sec": 21.0},
        {"date": (NOW - timedelta(days=4)).date().isoformat(), "sec": 20.0},
    ]


def test_progress_none_when_no_planned_past():
    workouts = [_wo(1, -3, "accel")]  # only a future workout
    r = st.match_sprint_sessions(workouts, [], NOW, plan_start=NOW - timedelta(days=10))
    p = r["progress"]
    assert p["adherence_pct"] is None
    assert p["latest_best_100m_sec"] is None
    assert p["best_100m_trend"] == []
