from datetime import datetime
import plan_generator as pg

START = datetime(2026, 7, 13)  # a Monday
MODEL = {
    "easy_pace_sec": 435, "easy_hr_ceiling": 160,
    "threshold_pace_sec": 335, "longest_run_28d_km": 2.88,
}


def test_plan_spans_requested_weeks_and_taper():
    out = pg.generate_plan(MODEL, 8, 27 * 60 + 22, START)
    weeks = {w["week_number"] for w in out["workouts"]}
    assert weeks == set(range(1, 9))
    race = [w for w in out["workouts"] if w["title"].startswith("Race day")]
    assert len(race) == 1 and race[0]["week_number"] == 8


def test_long_run_grows_at_most_1km_per_week():
    out = pg.generate_plan(MODEL, 10, 1620, START)
    longs = {}
    for w in out["workouts"]:
        if w["day_type"] == "long":
            longs[w["week_number"]] = w["target_distance_m"] / 1000.0
    # Guardrail: no week's long run exceeds the established peak by >1 km
    # (down weeks dip below peak; returning to peak afterwards is expected).
    peak = None
    for wk in sorted(longs):
        if peak is not None:
            assert longs[wk] <= peak + 1.0 + 1e-6, f"week {wk} long exceeds peak+1km"
        peak = max(peak or 0.0, longs[wk])
    assert max(longs.values()) <= pg.LONG_RUN_CAP_KM


def test_down_week_every_fourth_has_no_quality():
    out = pg.generate_plan(MODEL, 12, 1600, START)
    wk4 = [w for w in out["workouts"] if w["week_number"] == 4]
    assert all(w["day_type"] != "quality" for w in wk4)  # down week: no hard session


def test_no_quality_before_base_is_laid():
    out = pg.generate_plan(MODEL, 8, 1650, START)
    early_quality = [w for w in out["workouts"]
                     if w["day_type"] == "quality" and w["week_number"] < pg.QUALITY_START_WEEK
                     and not w["title"].startswith("Race")]
    assert early_quality == []


def test_long_run_is_the_longest_of_its_week():
    # A "long run" must never be shorter than that week's easy/quality runs —
    # even in early weeks when the runner's base is tiny.
    out = pg.generate_plan(MODEL, 8, 1650, START)
    by_week: dict[int, list] = {}
    for w in out["workouts"]:
        by_week.setdefault(w["week_number"], []).append(w)
    for wk, wos in by_week.items():
        longs = [w for w in wos if w["day_type"] == "long"]
        if not longs:  # taper week has no long run
            continue
        long_km = longs[0]["target_distance_m"] / 1000.0
        for w in wos:
            if w["day_type"] == "long" or not w.get("target_distance_m"):
                continue
            other_km = w["target_distance_m"] / 1000.0
            assert other_km <= long_km, (
                f"week {wk}: {w['day_type']} run {other_km}km exceeds long run {long_km}km"
            )


def test_easy_runs_carry_hr_ceiling():
    out = pg.generate_plan(MODEL, 8, 1650, START)
    for w in out["workouts"]:
        if w["day_type"] == "easy":
            assert w["hr_ceiling"] == 160
            assert w["pace_low_sec"] and w["pace_high_sec"]
