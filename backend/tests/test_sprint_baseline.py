from datetime import datetime, timedelta

import sprint_baseline as sb

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _effort(distance_target, time_seconds, days_ago):
    return {
        "distance_target": distance_target,
        "time_seconds": time_seconds,
        "start_date": NOW - timedelta(days=days_ago),
    }


def _interval_config(days_ago, fastest, slowest, total_reps, rep_durations):
    segments = [{"type": "rep", "duration_s": d} for d in rep_durations]
    # sprinkle a non-rep segment to ensure filtering works
    segments.insert(0, {"type": "recovery", "duration_s": 60})
    return {
        "start_date": NOW - timedelta(days=days_ago),
        "config": {
            "result": {
                "summary": {
                    "fastest_rep_pace": fastest,
                    "slowest_rep_pace": slowest,
                    "total_reps": total_reps,
                },
                "segments": segments,
            }
        },
    }


def test_speed_endurance_realistic():
    efforts = [
        _effort(100, 19.0, days_ago=30),
        _effort(100, 21.5, days_ago=60),
        _effort(200, 70.0, days_ago=45),
    ]
    configs = [
        _interval_config(
            days_ago=20,
            fastest=217.8,
            slowest=305.0,
            total_reps=6,
            rep_durations=[33, 35, 33, 39, 42, 46],
        )
    ]
    profile = sb.build_sprint_profile(efforts, configs, NOW)

    assert profile["best_100m_sec"] == 19.0
    assert profile["best_100m_date"] == (NOW - timedelta(days=30)).date().isoformat()
    assert profile["best_200m_sec"] == 70.0
    assert profile["top_speed_mps"] == round(100 / 19.0, 2)
    assert profile["interval_sessions"] == 1
    # fade = (305/217.8 - 1)*100 ~ 40.0
    assert profile["fade_pct"] == 40.0
    # decay slope of [33,35,33,39,42,46] vs index = 46/17.5 ~ 2.6
    assert profile["decay_sec_per_rep"] == 2.6
    assert profile["diagnosis"] == "speed_endurance"
    assert isinstance(profile["diagnosis_detail"], str) and profile["diagnosis_detail"]
    # references the fade number somewhere
    assert "40" in profile["diagnosis_detail"]


def test_insufficient_data_empty():
    profile = sb.build_sprint_profile([], [], NOW)
    assert profile["best_100m_sec"] is None
    assert profile["best_100m_date"] is None
    assert profile["best_200m_sec"] is None
    assert profile["top_speed_mps"] is None
    assert profile["interval_sessions"] == 0
    assert profile["fade_pct"] is None
    assert profile["decay_sec_per_rep"] is None
    assert profile["prior_speed_exposure"] is False
    assert profile["diagnosis"] == "insufficient_data"
    assert profile["diagnosis_detail"]
    assert profile["supporting_efforts"] == []


def test_best_100m_prefers_recent_within_365d():
    # An all-time fastest (17.5) but >365 days old; recent best is 19.0
    efforts = [
        _effort(100, 17.5, days_ago=400),
        _effort(100, 19.0, days_ago=30),
        _effort(100, 20.0, days_ago=100),
    ]
    profile = sb.build_sprint_profile(efforts, [], NOW)
    assert profile["best_100m_sec"] == 19.0
    assert profile["best_100m_date"] == (NOW - timedelta(days=30)).date().isoformat()


def test_best_100m_falls_back_to_alltime_when_no_recent():
    efforts = [
        _effort(100, 17.5, days_ago=400),
        _effort(100, 18.5, days_ago=500),
    ]
    profile = sb.build_sprint_profile(efforts, [], NOW)
    assert profile["best_100m_sec"] == 17.5
    assert profile["best_100m_date"] == (NOW - timedelta(days=400)).date().isoformat()


def test_prior_speed_exposure_true_and_false():
    cfg_recent = _interval_config(20, 200.0, 210.0, 4, [30, 31, 32, 33])
    cfg_recent2 = _interval_config(50, 200.0, 210.0, 4, [30, 31, 32, 33])
    cfg_old = _interval_config(400, 200.0, 210.0, 4, [30, 31, 32, 33])

    # Two recent -> True
    p_true = sb.build_sprint_profile([], [cfg_recent, cfg_recent2], NOW)
    assert p_true["prior_speed_exposure"] is True

    # One recent + one old -> False (only 1 within 365d)
    p_false = sb.build_sprint_profile([], [cfg_recent, cfg_old], NOW)
    assert p_false["prior_speed_exposure"] is False


def test_sessions_below_three_reps_ignored():
    # total_reps < 3 should not count toward interval_sessions / fade / decay
    small = _interval_config(20, 200.0, 260.0, 2, [30, 40])
    profile = sb.build_sprint_profile([], [small], NOW)
    assert profile["interval_sessions"] == 0
    assert profile["fade_pct"] is None
    assert profile["decay_sec_per_rep"] is None


def test_top_speed_diagnosis():
    # Slow 100m -> top_speed_mps < 5.2, no big fade -> top_speed
    efforts = [_effort(100, 20.0, days_ago=10)]
    cfg = _interval_config(20, 200.0, 210.0, 4, [30, 31, 32, 33])  # fade ~5%
    profile = sb.build_sprint_profile(efforts, [cfg], NOW)
    assert profile["top_speed_mps"] == round(100 / 20.0, 2)  # 5.0
    assert profile["top_speed_mps"] < 5.2
    assert profile["diagnosis"] == "top_speed"


def test_balanced_diagnosis():
    efforts = [_effort(100, 18.0, days_ago=10)]  # top_speed ~5.56
    cfg = _interval_config(20, 200.0, 210.0, 4, [30, 31, 32, 33])  # fade ~5%
    profile = sb.build_sprint_profile(efforts, [cfg], NOW)
    assert profile["diagnosis"] == "balanced"


def test_supporting_efforts_shape():
    efforts = [
        _effort(100, 19.0, days_ago=30),
        _effort(100, 21.5, days_ago=60),
        _effort(100, 22.0, days_ago=90),  # 3rd fastest 100 -> excluded (only 2)
        _effort(200, 70.0, days_ago=45),
    ]
    profile = sb.build_sprint_profile(efforts, [], NOW)
    se = profile["supporting_efforts"]
    assert len(se) == 3
    # sorted by distance then time
    assert [e["distance_m"] for e in se] == [100, 100, 200]
    assert se[0]["time_sec"] == 19.0
    assert se[1]["time_sec"] == 21.5
    assert se[2]["distance_m"] == 200
    assert se[0]["date"] == (NOW - timedelta(days=30)).date().isoformat()
    assert set(se[0].keys()) == {"distance_m", "time_sec", "date"}
