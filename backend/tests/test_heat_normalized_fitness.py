from datetime import datetime, timedelta

import fitness_model as fm

NOW = datetime(2026, 7, 14)
CEILING = 160


def _act(days_ago, km, pace_sec, hr=None, norm=None, aid=1):
    return {"id": aid, "start_date": NOW - timedelta(days=days_ago),
            "distance": km * 1000, "average_speed": 1000.0 / pace_sec,
            "average_heartrate": hr, "normalized_pace_sec": norm}


# --- easy pace is measured, not guessed -------------------------------------

def test_easy_pace_is_measured_from_easy_hr_runs_when_they_exist():
    # The bug this fixes: the plan guessed 7:15/km from threshold x 1.30 while
    # the runner's own easy-HR runs sat at 6:31.
    acts = [_act(60 + i * 5, 3.0, 404, hr=150, norm=391, aid=i) for i in range(5)]
    out = fm._estimate_easy_pace(acts, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["method"] == "measured"
    assert out["easy_pace_sec"] == 391          # the normalised pace, not 435
    assert out["easy_runs_used"] == 5


def test_it_measures_on_heat_normalised_pace_not_raw_pace():
    # Same effort, run in a monsoon: raw 6:59, cool-day equivalent 6:31.
    acts = [_act(30 + i, 3.0, 419, hr=150, norm=391, aid=i) for i in range(4)]
    out = fm._estimate_easy_pace(acts, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["easy_pace_sec"] == 391          # not 419 — weather is not fitness


def test_it_falls_back_to_the_estimate_when_there_is_nothing_to_measure():
    hard = [_act(2, 3.0, 396, hr=187, aid=i) for i in range(5)]
    out = fm._estimate_easy_pace(hard, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["method"] == "estimate"
    assert out["easy_pace_sec"] == 436          # 335 * 1.30


def test_two_easy_runs_is_an_anecdote_not_a_measurement():
    acts = [_act(10, 3.0, 390, hr=150, aid=1), _act(20, 3.0, 395, hr=152, aid=2)]
    out = fm._estimate_easy_pace(acts, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["method"] == "estimate"


def test_a_fitter_athlete_from_two_seasons_ago_is_out_of_scope():
    ancient = [_act(500 + i, 3.0, 343, hr=150, aid=i) for i in range(10)]
    out = fm._estimate_easy_pace(ancient, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["method"] == "estimate"          # too old to measure from


def test_raw_pace_is_used_when_a_run_has_no_weather_backfill():
    acts = [_act(30 + i, 3.0, 400, hr=150, norm=None, aid=i) for i in range(4)]
    out = fm._estimate_easy_pace(acts, CEILING, threshold_pace_sec=335, now=NOW)
    assert out["easy_pace_sec"] == 400


# --- threshold stops blaming the runner for the weather ----------------------

def test_threshold_uses_heat_normalised_pace():
    # A monsoon 6:37 that was really a 6:12 effort must not read as lost fitness.
    acts = [_act(5, 2.5, 397, norm=372, aid=1), _act(9, 2.0, 410, norm=385, aid=2)]
    assert fm._threshold_pace_sec(acts, NOW) == 372


# --- a stale measurement must not describe a runner who no longer exists ------

def test_a_pre_layoff_measurement_is_scaled_by_actual_fitness_loss():
    # Easy runs from ~11 weeks ago at 6:31 normalised, and a top end that has
    # since gone from 5:20 to 6:12 — the runner really is slower now.
    easy = [_act(80 + i * 5, 3.0, 404, hr=150, norm=391, aid=i) for i in range(4)]
    era_hard = [_act(85, 3.0, 330, norm=320, aid=50)]          # 5:20 back then
    now_hard = [_act(6, 2.5, 397, hr=187, norm=372, aid=51)]   # 6:12 today
    out = fm._estimate_easy_pace(easy + era_hard + now_hard, CEILING, 335, now=NOW)

    assert out["method"] == "measured_stale"
    assert out["measured_easy_pace_sec"] == 391       # what they DID run
    assert out["detraining_factor"] > 1.0
    assert out["easy_pace_sec"] > 391                 # ...but they're slower now
    assert out["staleness_days"] > fm.FRESH_EASY_DAYS


def test_a_fresh_measurement_is_taken_at_face_value():
    easy = [_act(5 + i, 3.0, 404, hr=150, norm=391, aid=i) for i in range(4)]
    out = fm._estimate_easy_pace(easy, CEILING, 335, now=NOW)
    assert out["method"] == "measured"
    assert out["easy_pace_sec"] == 391                # no decay applied


def test_getting_faster_never_slows_the_target():
    easy = [_act(80 + i, 3.0, 404, hr=150, norm=391, aid=i) for i in range(4)]
    era_hard = [_act(85, 3.0, 400, norm=400, aid=50)]
    now_hard = [_act(6, 2.5, 340, norm=330, aid=51)]   # much faster now
    out = fm._estimate_easy_pace(easy + era_hard + now_hard, CEILING, 335, now=NOW)
    assert out["easy_pace_sec"] == 391                 # unchanged, not sped up


def test_detraining_is_capped_so_one_bad_run_cannot_wreck_the_plan():
    easy = [_act(80 + i, 3.0, 404, hr=150, norm=391, aid=i) for i in range(4)]
    era_hard = [_act(85, 3.0, 300, norm=300, aid=50)]
    now_hard = [_act(6, 2.5, 600, norm=600, aid=51)]   # a disastrous jog
    out = fm._estimate_easy_pace(easy + era_hard + now_hard, CEILING, 335, now=NOW)
    assert out["detraining_factor"] == fm.MAX_DETRAINING


# --- the pace floor: below 7:30 you're shuffling, not running ----------------

def test_easy_pace_never_goes_below_the_shuffle_floor():
    easy = [_act(80 + i, 3.0, 404, hr=150, norm=391, aid=i) for i in range(4)]
    era_hard = [_act(85, 3.0, 300, norm=300, aid=50)]
    now_hard = [_act(6, 2.5, 600, norm=600, aid=51)]   # heavy detraining
    out = fm._estimate_easy_pace(easy + era_hard + now_hard, CEILING, 335, now=NOW)
    assert out["detrained_easy_pace_sec"] > fm.EASY_PACE_FLOOR_SEC   # the raw maths
    assert out["easy_pace_sec"] == fm.EASY_PACE_FLOOR_SEC            # ...but we floor it
    assert out["pace_floored"] is True


def test_the_estimate_branch_respects_the_floor_too():
    hard = [_act(2, 3.0, 396, hr=187, aid=i) for i in range(5)]
    out = fm._estimate_easy_pace(hard, CEILING, threshold_pace_sec=420, now=NOW)
    assert out["easy_pace_sec"] <= fm.EASY_PACE_FLOOR_SEC   # 420*1.3 = 546 -> floored


def test_plan_bands_never_prescribe_slower_than_the_floor():
    import plan_generator as pg
    model = {"easy_pace_sec": 463, "easy_hr_ceiling": 160,   # 7:43, past the floor
             "threshold_pace_sec": 316, "longest_run_28d_km": 2.88}
    out = pg.generate_plan(model, 8, 1560, datetime(2026, 7, 20))
    for w in out["workouts"]:
        if w["day_type"] in ("easy", "long", "strides") and w["pace_high_sec"]:
            assert w["pace_high_sec"] <= fm.EASY_PACE_FLOOR_SEC


def test_the_easy_run_tells_you_to_walk_rather_than_shuffle():
    import plan_generator as pg
    model = {"easy_pace_sec": 450, "easy_hr_ceiling": 160,
             "threshold_pace_sec": 316, "longest_run_28d_km": 2.88}
    out = pg.generate_plan(model, 8, 1560, datetime(2026, 7, 20))
    easy = next(w for w in out["workouts"] if w["day_type"] == "easy")
    assert "walk breaks" in easy["description"]
    assert "HR ≤160 bpm is the real rule" in easy["description"]
