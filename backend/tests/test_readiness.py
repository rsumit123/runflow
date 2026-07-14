import heat
import readiness as rd

# Shaped like the real Garmin payloads probed off the user's watch.
READY = [{"score": 51, "level": "MODERATE", "sleepScore": 80,
          "feedbackShort": "BOOSTED_BY_GOOD_SLEEP"}]
SLEEP = {"dailySleepDTO": {"sleepTimeSeconds": 27600,
                           "sleepScores": {"overall": {"value": 80}}}}
BB = [{"bodyBatteryValuesArray": [[1, 20], [2, 88], [3, 79]]}]
RHR = {"allMetrics": {"metricsMap": {"WELLNESS_RESTING_HEART_RATE": [{"value": 53.0}]}}}
# The user's real HRV state: a reading, but Garmin has no baseline for it yet.
HRV_ONBOARDING = {"hrvSummary": {"lastNightAvg": 64, "status": "NONE", "baseline": None}}
HRV_BALANCED = {"hrvSummary": {"lastNightAvg": 64, "status": "BALANCED",
                               "baseline": {"lowUpper": 50}, "weeklyAvg": 51}}


def _ready(score):
    return [{"score": score, "level": "X"}]


# --- reading the signals ----------------------------------------------------

def test_hrv_without_a_baseline_is_reported_but_never_scored():
    out = rd.assess(READY, HRV_ONBOARDING, SLEEP, BB, RHR)
    hrv = next(f for f in out["factors"] if f["name"] == "HRV")
    assert hrv["verdict"] == "unknown"      # not "good", not "bad"
    assert "still learning" in hrv["detail"]


def test_hrv_counts_once_garmin_has_a_baseline():
    out = rd.assess(READY, HRV_BALANCED, SLEEP, BB, RHR)
    hrv = next(f for f in out["factors"] if f["name"] == "HRV")
    assert hrv["verdict"] == "good"


def test_body_battery_uses_the_days_peak_not_the_latest_value():
    out = rd.assess(READY, HRV_ONBOARDING, SLEEP, BB, RHR)
    bb = next(f for f in out["factors"] if f["name"] == "Body Battery")
    assert "88" in bb["value"]              # peak, not the trailing 79
    assert bb["verdict"] == "good"


def test_short_sleep_is_flagged_bad():
    short = {"dailySleepDTO": {"sleepTimeSeconds": 4 * 3600}}
    out = rd.assess(READY, None, short, None, None)
    s = next(f for f in out["factors"] if f["name"] == "Sleep")
    assert s["verdict"] == "bad"


def test_missing_metrics_degrade_gracefully():
    out = rd.assess(None, None, None, None, None)
    assert out["available"] is False
    assert out["level"] == "unknown"


# --- the call ---------------------------------------------------------------

def test_low_readiness_downgrades_a_hard_session():
    a = rd.assess(_ready(35), None, None, None, None)
    out = rd.adjust("quality", a)
    assert out["action"] == "downgrade"
    assert out["swap_to"] == "easy"
    assert "35/100" in out["reason"]


def test_low_readiness_leaves_an_easy_day_alone():
    a = rd.assess(_ready(35), None, None, None, None)
    assert rd.adjust("easy", a)["action"] == "keep"


def test_very_low_readiness_calls_for_rest():
    a = rd.assess(_ready(20), None, None, None, None)
    assert rd.adjust("easy", a)["action"] == "rest"


def test_the_users_actual_readiness_of_51_keeps_the_session():
    a = rd.assess(READY, HRV_ONBOARDING, SLEEP, BB, RHR)
    assert a["score"] == 51 and a["level"] == "moderate"
    assert rd.adjust("quality", a)["action"] == "keep"


def test_high_readiness_greenlights_quality():
    a = rd.assess(_ready(85), None, None, None, None)
    out = rd.adjust("quality", a)
    assert out["action"] == "keep" and "primed" in out["reason"]


def test_feeling_good_on_an_easy_day_does_not_license_running_it_hard():
    # The classic trap: high readiness + easy day -> people race their easy run.
    a = rd.assess(_ready(85), None, None, None, None)
    out = rd.adjust("easy", a)
    assert out["action"] == "keep"
    assert "easy" in out["reason"].lower()
    # ...and the verdict must not contradict the HIGH badge the UI shows.
    assert "normal" not in out["reason"]


def test_the_reason_names_the_factor_that_drove_the_call():
    bad_sleep = {"dailySleepDTO": {"sleepTimeSeconds": 4 * 3600}}
    a = rd.assess(_ready(35), None, bad_sleep, None, None)
    assert "sleep" in rd.adjust("quality", a)["reason"].lower()


def test_no_readiness_data_never_invents_an_adjustment():
    a = rd.assess(None, None, None, None, None)
    out = rd.adjust("quality", a)
    assert out["action"] == "keep" and out["confidence"] == "none"


def test_sprint_hard_days_downgrade_to_technique():
    a = rd.assess(_ready(35), None, None, None, None)
    assert rd.adjust("max_velocity", a)["swap_to"] == "technique"


# --- the storable facts behind the assessment -------------------------------

def test_facts_flattens_the_payloads_for_the_daily_cache():
    f = rd.facts({"readiness": READY, "hrv": HRV_ONBOARDING, "sleep": SLEEP,
                  "body_battery": BB, "rhr": RHR})
    assert f["sleep_hours"] == 7.7
    assert f["sleep_score"] == 80
    assert f["body_battery_peak"] == 88
    assert f["hrv_last_night"] == 64
    assert f["hrv_status"] == "NONE"     # stored, so we can see the day it changes
    assert f["resting_hr"] == 53


def test_facts_survives_a_watch_that_reports_nothing():
    f = rd.facts({})
    assert all(v is None for v in f.values())


# --- heat -------------------------------------------------------------------

def test_cool_dry_air_costs_nothing():
    out = heat.adjust(12, 5, 420, 455)
    assert out["adjusted"] is False
    assert out["penalty_sec"] == 0
    assert out["level"] == "ideal"


def test_jamshedpur_monsoon_is_severe_and_widens_the_band():
    # 30 C air, 26 C dew point — hot AND saturated. Index = 86 + 78.8 = 164.8.
    out = heat.adjust(30, 26, 420, 455)
    assert out["level"] == "severe"
    assert out["adjusted"] is True
    assert out["stress_index"] == 165
    assert out["penalty_sec"] == 29          # ~7.0%, interpolated — NOT the 8% band ceiling


def test_the_penalty_is_interpolated_not_rounded_up_to_the_worst_case():
    # 160-170 on the table means 6-8%. An index of 161 must not be charged 8%.
    low = heat.adjust(28.3, 22.2, 420, 455)   # index ~161
    high = heat.adjust(33, 27, 420, 455)      # index ~172
    assert low["slowdown_pct"] < 6.5
    assert high["slowdown_pct"] > low["slowdown_pct"]


def test_humidity_not_just_temperature_drives_the_penalty():
    dry = heat.adjust(30, 10, 420, 455)      # same 30 C, dry air
    humid = heat.adjust(30, 26, 420, 455)
    assert humid["penalty_sec"] > dry["penalty_sec"]


def test_the_explanation_says_same_effort_not_easier():
    out = heat.adjust(30, 26, 420, 455)
    assert "SAME effort" in out["detail"]


def test_a_humid_day_blames_the_humidity_not_the_sun():
    # The runner's own words: "it's rainy season, there's hardly any sun."
    out = heat.adjust(30, 26, 420, 455)
    assert "not the sun" in out["detail"]
    assert "overcast" in out["detail"].lower()


def test_a_trivial_penalty_is_not_worth_changing_targets_for():
    out = heat.adjust(18, 10, 420, 455)
    assert out["adjusted"] is False
    assert out["pace_low_sec"] == 420        # untouched


def test_heat_adjustment_needs_a_band_to_adjust():
    out = heat.adjust(30, 26, None, None)
    assert out["adjusted"] is False
