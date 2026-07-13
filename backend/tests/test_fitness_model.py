from datetime import datetime, timedelta
import fitness_model as fm

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _run(days_ago, dist_m, pace_sec, hr=None, max_hr=None, rid=1, name="Run"):
    speed = 1000.0 / pace_sec  # m/s
    return {
        "id": rid, "name": name, "distance": dist_m,
        "start_date": NOW - timedelta(days=days_ago),
        "average_speed": speed, "average_heartrate": hr, "max_heartrate": max_hr,
    }


def test_classify_hr_based():
    # max HR 200: easy <150, gray 150-176, hard >176
    assert fm.classify_run(140, 3.0, 200, 420)["zone"] == "easy"
    assert fm.classify_run(160, 3.0, 200, 420)["zone"] == "gray"
    assert fm.classify_run(185, 3.0, 200, 420)["zone"] == "hard"
    assert fm.classify_run(160, 3.0, 200, 420)["basis"] == "hr"


def test_classify_pace_based_when_no_hr():
    # easy pace 450s/km: slower(>=440) => easy, faster => gray
    assert fm.classify_run(None, 1000 / 470, 200, 450)["zone"] == "easy"
    assert fm.classify_run(None, 1000 / 400, 200, 450)["zone"] == "gray"
    assert fm.classify_run(None, 1000 / 400, 200, 450)["basis"] == "pace"


def test_fitness_model_volume_and_acwr():
    acts = [_run(d, 3000, 360, rid=i) for i, d in enumerate([1, 3, 5, 9, 12, 20, 26])]
    m = fm.build_fitness_model(acts, NOW)
    assert m["longest_run_28d_km"] == 3.0
    assert m["acwr"] is not None
    assert m["easy_hr_ceiling"] == round(0.76 * fm.DEFAULT_MAX_HR)  # no HR -> default


def test_easy_hr_ceiling_from_observed_max():
    acts = [_run(2, 3000, 360, hr=185, max_hr=207, rid=1)] * 1
    m = fm.build_fitness_model(acts, NOW)
    assert m["athlete_max_hr"] == 207
    assert m["easy_hr_ceiling"] == round(0.76 * 207)


def test_gray_zone_all_hard_history():
    # 5 recent runs all at ~93% max HR -> all hard/gray, pct_gray high or hard
    acts = [_run(d, 3000, 360, hr=188, max_hr=207, rid=i) for i, d in enumerate([1, 3, 5, 8, 11])]
    m = fm.build_fitness_model(acts, NOW)
    g = fm.gray_zone_summary(acts, NOW, m)
    assert g["counts_14d"]["easy"] == 0
    assert len(g["recent_runs"]) == 5
    assert g["recent_runs"][0]["zone"] in ("gray", "hard")


def test_warnings_force_easy_and_no_easy():
    acts = [_run(d, 3000, 360, hr=190, max_hr=207, rid=i) for i, d in enumerate([1, 2, 4])]
    m = fm.build_fitness_model(acts, NOW)
    g = fm.gray_zone_summary(acts, NOW, m)
    codes = {w["code"] for w in fm.build_warnings(acts, NOW, m, g)}
    assert "force_easy" in codes
    assert "no_easy" in codes


def test_training_report_shape():
    acts = [_run(d, 3000, 360, hr=180, max_hr=205, rid=i) for i, d in enumerate([1, 3, 6])]
    r = fm.training_report(acts, NOW)
    assert set(r) == {"fitness_model", "gray_zone", "warnings"}
    assert "pct_gray_14d" in r["gray_zone"]
