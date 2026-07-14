from datetime import datetime, timedelta

import pace_adaptation as pa

NOW = datetime(2026, 7, 14)
CEILING = 160
SNAPSHOT = {"easy_hr_ceiling": CEILING, "easy_pace_sec": 435,
            "easy_pace_method": "estimate", "threshold_pace_sec": 335}


def _act(days_ago: float, km: float, pace_sec: int, hr=None, aid=1):
    return {"id": aid, "start_date": NOW - timedelta(days=days_ago),
            "distance": km * 1000, "average_speed": 1000.0 / pace_sec,
            "average_heartrate": hr}


# --- measuring easy pace ----------------------------------------------------

def test_runs_above_the_hr_ceiling_are_not_easy_runs():
    # The user's real situation: every run at 180-187 bpm, ceiling 160.
    acts = [_act(2, 2.88, 396, hr=187), _act(4, 1.44, 378, hr=180),
            _act(6, 2.45, 395, hr=187)]
    out = pa.measure_easy_pace(acts, CEILING, NOW)
    assert out["measured_easy_pace_sec"] is None
    assert out["method"] == "no_easy_runs"
    assert out["confidence"] == "none"
    assert out["easy_runs_found"] == 0
    assert len(out["too_hard"]) == 3  # and we can SHOW them why


def test_easy_pace_is_measured_from_runs_under_the_ceiling():
    acts = [_act(2, 3.0, 400, hr=150), _act(5, 3.0, 410, hr=155),
            _act(9, 3.0, 390, hr=158)]
    out = pa.measure_easy_pace(acts, CEILING, NOW)
    assert out["measured_easy_pace_sec"] == 400  # mean of 400/410/390
    assert out["method"] == "measured"
    assert out["confidence"] == "medium"  # 3 runs
    assert len(out["evidence"]) == 3


def test_confidence_grows_with_the_number_of_easy_runs():
    def n_runs(n):
        return pa.measure_easy_pace(
            [_act(i + 1, 3.0, 400, hr=150, aid=i) for i in range(n)], CEILING, NOW)
    assert n_runs(0)["confidence"] == "none"
    assert n_runs(1)["confidence"] == "low"
    assert n_runs(3)["confidence"] == "medium"
    assert n_runs(5)["confidence"] == "high"


def test_a_few_bpm_over_the_ceiling_still_counts_as_easy():
    out = pa.measure_easy_pace([_act(2, 3.0, 400, hr=CEILING + 2)], CEILING, NOW)
    assert out["easy_runs_found"] == 1


def test_stale_runs_fall_outside_the_evidence_window():
    out = pa.measure_easy_pace(
        [_act(pa.LOOKBACK_DAYS + 5, 3.0, 400, hr=150)], CEILING, NOW)
    assert out["measured_easy_pace_sec"] is None


# --- calibration ------------------------------------------------------------

def test_no_easy_runs_produces_the_explaining_insight_not_a_silent_nochange():
    acts = [_act(2, 2.88, 396, hr=187), _act(4, 2.4, 378, hr=180)]
    out = pa.calibrate(SNAPSHOT, acts, NOW)
    assert out["has_changes"] is False
    ins = next(i for i in out["insights"] if i["kind"] == "no_easy_runs")
    assert "160" in ins["detail"]          # names the ceiling
    assert "180" in ins["detail"]          # names what they actually ran at
    assert ins["evidence"]                 # and shows the runs


def test_measured_improvement_moves_the_easy_band_faster():
    # Plan assumed 7:15/km easy; runner is doing 6:40/km at easy HR.
    acts = [_act(i + 1, 3.0, 400, hr=150, aid=i) for i in range(4)]
    out = pa.calibrate(SNAPSHOT, acts, NOW)
    assert out["has_changes"] is True
    ch = next(c for c in out["changes"] if c["field"] == "easy_pace")
    assert ch["delta_sec"] == 35           # 435 -> 400
    assert out["proposed"]["easy_low_sec"] == 385   # 400 - 15
    assert out["proposed"]["easy_high_sec"] == 420  # 400 + 20
    assert "faster" in ch["reason"]


def test_a_small_wobble_is_noise_and_changes_nothing():
    acts = [_act(i + 1, 3.0, 430, hr=150, aid=i) for i in range(4)]  # 5 s/km off
    out = pa.calibrate(SNAPSHOT, acts, NOW)
    assert out["has_changes"] is False
    assert any(i["kind"] == "easy_pace_on_track" for i in out["insights"])


def test_one_easy_run_is_not_enough_to_retarget():
    out = pa.calibrate(SNAPSHOT, [_act(2, 3.0, 380, hr=150)], NOW)
    assert out["has_changes"] is False
    assert any(i["kind"] == "easy_pace_low_confidence" for i in out["insights"])


def test_top_end_fitness_is_reported_but_never_silently_rewrites_the_goal():
    # Slower than at plan start (a training gap) -> insight, but no workout edits.
    acts = [_act(2, 2.88, 396, hr=187), _act(4, 2.45, 395, hr=187)]
    out = pa.calibrate(SNAPSHOT, acts, NOW)
    ins = next(i for i in out["insights"] if i["kind"] == "threshold_slower")
    assert "5:35" in ins["detail"]      # what the plan assumed (335 s/km)
    assert not any(c["field"] == "threshold_pace" for c in out["changes"])


def test_every_insight_carries_its_confidence_and_evidence():
    acts = [_act(i + 1, 3.0, 400, hr=150, aid=i) for i in range(4)]
    out = pa.calibrate(SNAPSHOT, acts, NOW)
    assert out["insights"]
    for i in out["insights"]:
        assert i["confidence"] in ("none", "low", "medium", "high")
        assert "detail" in i and i["title"]


def test_calibration_reports_what_the_plan_had_assumed():
    out = pa.calibrate(SNAPSHOT, [], NOW)
    assert out["plan_assumed"]["easy_pace"] == "7:15"
    assert out["plan_assumed"]["easy_pace_method"] == "estimate"


# --- retargeting workouts ---------------------------------------------------

def test_retarget_rewrites_band_and_the_garmin_step_targets():
    structure = {"warmup": "w", "cooldown": "c", "steps": [
        {"type": "warmup", "end_kind": "time", "end_value": 120, "target_kind": "none"},
        {"type": "run", "end_kind": "distance", "end_value": 3000,
         "target_kind": "pace", "pace_low_sec": 420, "pace_high_sec": 455},
    ]}
    out = pa.retarget_workout("easy", structure, 400)
    assert (out["pace_low_sec"], out["pace_high_sec"]) == (385, 420)
    run = out["structure"]["steps"][1]
    assert (run["pace_low_sec"], run["pace_high_sec"]) == (385, 420)
    assert out["structure"]["steps"][0]["target_kind"] == "none"  # warmup untouched


def test_long_runs_get_the_easier_band():
    out = pa.retarget_workout("long", None, 400)
    assert (out["pace_low_sec"], out["pace_high_sec"]) == (400, 430)


def test_goal_paced_sessions_are_never_retargeted_by_fitness():
    # Quality/race ride on the runner's GOAL, not their current fitness.
    assert pa.retarget_workout("quality", None, 400) is None
    assert pa.retarget_workout("rest", None, 400) is None


def test_retarget_does_not_mutate_the_original_structure():
    structure = {"steps": [{"type": "run", "target_kind": "pace",
                            "pace_low_sec": 420, "pace_high_sec": 455}]}
    pa.retarget_workout("easy", structure, 400)
    assert structure["steps"][0]["pace_low_sec"] == 420
