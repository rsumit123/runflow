import training_effect as te


def test_tempo_is_a_high_aerobic_warn_stimulus():
    c = te.classify("TEMPO")
    assert c["name"] == "Tempo"
    assert c["tier"] == "high_aerobic"
    assert c["tone"] == "warn"


def test_base_and_recovery_are_the_easy_zone():
    assert te.is_easy_stimulus("BASE") is True
    assert te.is_easy_stimulus("RECOVERY") is True
    assert te.is_easy_stimulus("TEMPO") is False
    assert te.is_easy_stimulus("VO2MAX") is False


def test_unknown_label_degrades_to_none():
    assert te.classify(None) is None
    assert te.is_easy_stimulus(None) is None


def test_an_unrecognised_label_is_still_shown_not_dropped():
    c = te.classify("SOME_NEW_LABEL")
    assert c["name"] == "Some_New_Label"      # rendered, not lost


# --- the cross-check against our own grade ----------------------------------

def test_garmin_tempo_agrees_with_our_not_easy_grade():
    # Today's real run: we said hr_drift, Garmin said TEMPO.
    x = te.cross_check("easy", "hr_drift", "TEMPO")
    assert x["agree"] is True
    assert "high-aerobic" in x["note"]
    assert x["garmin"]["name"] == "Tempo"


def test_both_calling_it_easy_agree():
    x = te.cross_check("easy", "on_target", "BASE")
    assert x["agree"] is True
    assert "matches our read" in x["note"]


def test_disagreement_when_garmin_says_easy_but_we_flagged_it():
    x = te.cross_check("easy", "hr_drift", "RECOVERY")
    assert x["agree"] is False
    assert "trust how it felt" in x["note"]


def test_no_cross_check_on_a_quality_day():
    # Quality days are supposed to be hard — nothing to reconcile.
    assert te.cross_check("quality", "on_target", "VO2MAX") is None


def test_no_cross_check_without_a_garmin_label():
    assert te.cross_check("easy", "on_target", None) is None
