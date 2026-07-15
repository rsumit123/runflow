"""
Interpret Garmin's Primary Benefit (Firstbeat Training Effect) and cross-check it
against our own easy/hard grading.

Garmin's label is personalised to the athlete's physiology, which makes it a
genuinely useful second opinion on "was that easy run actually easy?". But it is
also a black box, so we never let it REPLACE our transparent grade — we show both
and flag when they disagree.
"""
from __future__ import annotations

from typing import Any, Optional

# Garmin's primary-benefit buckets, coarsest-to-finest, with the tier the label
# belongs to. Green = the low-aerobic zone where base is built; orange = high
# aerobic (already a workout); purple = anaerobic.
TIER = {
    "RECOVERY": ("Recovery", "low_aerobic", "good"),
    "BASE": ("Base", "low_aerobic", "good"),
    "TEMPO": ("Tempo", "high_aerobic", "warn"),
    "LACTATE_THRESHOLD": ("Threshold", "high_aerobic", "warn"),
    "THRESHOLD": ("Threshold", "high_aerobic", "warn"),
    "VO2MAX": ("VO2 Max", "high_aerobic", "bad"),
    "VO2_MAX": ("VO2 Max", "high_aerobic", "bad"),
    "ANAEROBIC_CAPACITY": ("Anaerobic", "anaerobic", "bad"),
    "SPRINT": ("Sprint", "anaerobic", "bad"),
    "NONE": ("Minimal", "low_aerobic", "neutral"),
    "MINOR_BENEFIT": ("Minimal", "low_aerobic", "neutral"),
}

# Day types that are SUPPOSED to be easy. On those, a high-aerobic-or-harder
# Garmin verdict means the run wasn't the easy stimulus it was meant to be.
EASY_DAY_TYPES = {"easy", "long", "strides"}


def classify(label: Optional[str]) -> Optional[dict[str, Any]]:
    """Turn a raw Garmin label into {name, tier, tone}, or None if we don't have one."""
    if not label:
        return None
    name, tier, tone = TIER.get(label.upper(), (label.title(), "high_aerobic", "warn"))
    return {"label": label, "name": name, "tier": tier, "tone": tone}


def is_easy_stimulus(label: Optional[str]) -> Optional[bool]:
    """Did Garmin judge this an easy (low-aerobic) stimulus? None if unknown."""
    c = classify(label)
    if c is None:
        return None
    return c["tier"] == "low_aerobic"


def cross_check(day_type: Optional[str], our_compliance: Optional[str],
                label: Optional[str]) -> Optional[dict[str, Any]]:
    """Compare our easy/hard verdict with Garmin's, for an easy-intended day.

    Returns a small dict the UI can render, or None when there's nothing to say
    (not an easy day, or no Garmin label).
    """
    c = classify(label)
    if c is None or day_type not in EASY_DAY_TYPES:
        return None

    garmin_easy = c["tier"] == "low_aerobic"
    # Our grade counts the run as easy only when it was on_target.
    we_called_easy = our_compliance == "on_target"

    if garmin_easy and we_called_easy:
        agree, note = True, (
            f"Garmin logged this as {c['name'].lower()} — a genuinely easy stimulus. "
            "That matches our read: you kept it easy."
        )
    elif not garmin_easy and not we_called_easy:
        agree, note = True, (
            f"Garmin independently logged this as {c['name']} — a high-aerobic effort, not "
            "the easy stimulus this day was meant to be. That agrees with our read, and it's "
            "personalised to your physiology, so it's a strong second opinion: this wasn't easy yet."
        )
    elif garmin_easy and not we_called_easy:
        agree, note = False, (
            f"Our HR read flagged this as harder than easy, but Garmin logged it as {c['name']} — "
            "a genuinely easy stimulus. When they disagree, trust how it felt: this may have been "
            "easier than the raw number suggested."
        )
    else:
        agree, note = False, (
            f"We graded this easy, but Garmin logged it as {c['name']} — a high-aerobic effort. "
            "Worth easing off a touch more next time."
        )

    return {"garmin": c, "agree": agree, "note": note}
