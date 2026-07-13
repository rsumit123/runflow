from datetime import datetime
from collections import defaultdict

import sprint_plan_generator as spg

START = datetime(2026, 7, 13)  # a Monday
PROFILE = {
    "diagnosis": "speed_endurance",
    "prior_speed_exposure": True,
    "best_100m_sec": 19.0,
    "fade_pct": 40.0,
}
CNS_TYPES = {"accel", "max_velocity", "speed_endurance", "test"}


def _by_week(out):
    weeks = defaultdict(list)
    for w in out["workouts"]:
        weeks[w["week_number"]].append(w)
    return weeks


def test_goal_fields():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    assert out["goal_100m_sec"] == 17.7
    assert out["goal_date"] == out["workouts"][-1]["date"]


def test_sessions_per_week_and_pattern():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    weeks = _by_week(out)
    assert set(weeks) == set(range(1, 9))
    counts = {wk: len(ws) for wk, ws in weeks.items()}
    # 3 normal weeks, 2 in deload (wk4), 2 in taper (wk8)
    assert counts == {1: 3, 2: 3, 3: 3, 4: 2, 5: 3, 6: 3, 7: 3, 8: 2}

    types = {wk: [w["day_type"] for w in ws] for wk, ws in weeks.items()}
    assert types[1] == ["test", "technique", "accel"]
    assert types[2] == ["accel", "max_velocity", "speed_endurance"]
    assert types[3] == ["accel", "max_velocity", "speed_endurance"]
    assert types[4] == ["accel", "max_velocity"]  # deload: no speed_endurance
    assert types[7] == ["accel", "max_velocity", "speed_endurance"]
    assert types[8] == ["technique", "test"]  # taper


def test_48h_spacing_between_sessions():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    weeks = _by_week(out)
    for wk, ws in weeks.items():
        dates = sorted(w["date"] for w in ws)
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        assert all(g >= 2 for g in gaps), f"week {wk} has sessions <48h apart: {gaps}"


def test_first_is_baseline_test_last_is_goal_test():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    weeks = _by_week(out)
    first = weeks[1][0]
    assert first["day_type"] == "test"
    last = out["workouts"][-1]
    assert last["day_type"] == "test"
    assert last["structure"]["main_set"][0]["distance_m"] == 100


def test_deload_has_lower_volume_than_prior_week():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    weeks = _by_week(out)

    def week_vol(wk):
        return sum(w["structure"]["total_volume_m"] for w in weeks[wk])

    assert week_vol(4) < week_vol(3)


def test_effort_caps():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    for w in out["workouts"]:
        for m in w["structure"]["main_set"]:
            assert m["effort_pct"] <= 100
        if w["day_type"] == "max_velocity":
            if w["week_number"] < 3:
                assert all(m["effort_pct"] < 100 for m in w["structure"]["main_set"]), \
                    f"max_velocity hit 100% in week {w['week_number']}"


def test_structure_invariants():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    for w in out["workouts"]:
        s = w["structure"]
        assert s is not None
        assert s["total_volume_m"] == sum(m["reps"] * m["distance_m"] for m in s["main_set"])
        assert len(s["cues"]) >= 1
        assert s["warmup"] == spg.WARMUP
        # spec-required nullable fields
        for k in ("target_distance_m", "pace_low_sec", "pace_high_sec", "hr_ceiling"):
            assert w[k] is None


def test_speed_endurance_emphasis():
    out = spg.generate_sprint_plan(PROFILE, 8, 17.7, START)
    weeks = _by_week(out)
    # week 3 is a development week with both accel and speed_endurance
    accel = next(w for w in weeks[3] if w["day_type"] == "accel")
    se = next(w for w in weeks[3] if w["day_type"] == "speed_endurance")
    assert se["structure"]["total_volume_m"] > accel["structure"]["total_volume_m"]


def test_no_prior_exposure_keeps_week2_foundation():
    profile = dict(PROFILE, prior_speed_exposure=False)
    out = spg.generate_sprint_plan(profile, 8, 17.7, START)
    weeks = _by_week(out)
    types = [w["day_type"] for w in weeks[2]]
    assert "max_velocity" not in types  # no max-velocity yet without prior exposure
