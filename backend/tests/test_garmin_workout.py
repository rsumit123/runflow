from datetime import datetime

import garmin_workout as gw
import plan_generator as pg

START = datetime(2026, 7, 13)  # Monday
MODEL = {"easy_pace_sec": 435, "easy_hr_ceiling": 160,
         "threshold_pace_sec": 335, "longest_run_28d_km": 2.88}


def _steps_for(day_type, weeks=8, target=1642):
    out = pg.generate_plan(MODEL, weeks, target, START)
    for w in out["workouts"]:
        if w["day_type"] == day_type and (w.get("structure") or {}).get("steps"):
            return w["structure"]["steps"], w
    raise AssertionError(f"no {day_type} workout with steps")


def test_easy_run_has_warmup_run_cooldown_steps():
    steps, w = _steps_for("easy")
    kinds = [s["type"] for s in steps]
    assert kinds == ["warmup", "run", "cooldown"]
    run = steps[1]
    assert run["end_kind"] == "distance"
    assert run["end_value"] == w["target_distance_m"]
    assert run["target_kind"] == "pace"
    assert run["pace_low_sec"] == w["pace_low_sec"]


def test_quality_session_has_a_repeat_of_400s():
    steps, _ = _steps_for("quality")
    rep = next(s for s in steps if s["type"] == "repeat")
    assert rep["iterations"] == 6
    work, recov = rep["steps"]
    assert work["end_kind"] == "distance" and work["end_value"] == 400
    assert work["target_kind"] == "pace"
    assert recov["type"] == "recovery" and recov["end_value"] == 90


def test_pace_band_converts_to_garmin_speeds_m_per_s():
    # 7:00-7:35/km  ->  min speed 1000/455 = 2.198, max speed 1000/420 = 2.381
    steps = [{"type": "run", "end_kind": "distance", "end_value": 2500,
              "target_kind": "pace", "pace_low_sec": 420, "pace_high_sec": 455, "note": None}]
    wo = gw.build_running_workout("Easy run", steps)
    d = wo.to_dict()
    step = d["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert abs(step["targetValueOne"] - 2.198) < 0.01   # slower bound = min speed
    assert abs(step["targetValueTwo"] - 2.381) < 0.01   # faster bound = max speed
    assert step["endCondition"]["conditionTypeKey"] == "distance"
    assert step["endConditionValue"] == 2500.0


def test_repeat_group_survives_conversion():
    steps, _ = _steps_for("quality")
    wo = gw.build_running_workout("Speed session", steps)
    d = wo.to_dict()
    top = d["workoutSegments"][0]["workoutSteps"]
    grp = next(s for s in top if s.get("type") == "RepeatGroupDTO" or s.get("numberOfIterations"))
    assert grp["numberOfIterations"] == 6
    assert len(grp["workoutSteps"]) == 2


def test_no_target_step_uses_no_target():
    steps = [{"type": "warmup", "end_kind": "time", "end_value": 120,
              "target_kind": "none", "pace_low_sec": None, "pace_high_sec": None, "note": "jog"}]
    d = gw.build_running_workout("W", steps).to_dict()
    step = d["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "no.target"
    assert step["endCondition"]["conditionTypeKey"] == "time"


def test_pushed_workouts_are_prefixed_so_garmin_coach_is_distinguishable():
    steps = [{"type": "warmup", "end_kind": "time", "end_value": 120, "target_kind": "none"}]
    d = gw.build_running_workout("Easy run", steps).to_dict()
    assert d["workoutName"] == "RunFlow — Easy run"


def test_prefix_is_not_applied_twice_on_a_re_push():
    assert gw.workout_name("RunFlow — Easy run") == "RunFlow — Easy run"


def test_prefixed_name_is_capped_at_garmin_limit():
    name = gw.workout_name("x" * 200)
    assert len(name) == gw.MAX_NAME_LEN
    assert name.startswith(gw.NAME_PREFIX)


def test_duration_estimate_counts_repeats():
    steps = [
        {"type": "warmup", "end_kind": "time", "end_value": 120, "target_kind": "none"},
        {"type": "repeat", "iterations": 4, "steps": [
            {"type": "run", "end_kind": "time", "end_value": 60, "target_kind": "none"},
            {"type": "recovery", "end_kind": "time", "end_value": 30, "target_kind": "none"},
        ]},
    ]
    assert gw.estimate_seconds(steps) == 120 + 4 * 90
