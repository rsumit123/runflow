"""
Recalibrate a live plan from the runs that have actually landed.

Design rule: NEVER assume progress. A plan that pencils in "you'll be 10 s/km
faster by week 6" is guessing, and when the runner doesn't hit it the plan is
silently wrong. Instead we measure what the runner is really doing and move the
targets only when the evidence supports it — and we hand back every number we
used, so the adjustment can be audited rather than trusted.

Everything here is pure: activities in, calibration out. No DB, no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

LOOKBACK_DAYS = 42          # how far back we look for evidence
HISTORICAL_MAX_DAYS = 365   # "have you done this lately" — not "ever"
MIN_DIST_M = 1000           # ignore sub-1 km jogs — too noisy to read a pace from
HR_TOLERANCE = 3            # a few bpm over the ceiling still counts as easy
MIN_THRESHOLD_DIST_M = 1500 # a "hard" effort has to be a real sustained one

# How many easy-HR runs we need before we trust a measured easy pace.
CONFIDENCE_BY_N = [(5, "high"), (3, "medium"), (1, "low")]

MEANINGFUL_EASY_SHIFT_SEC = 10   # ignore sub-10 s/km wobble — that's noise, not fitness
MEANINGFUL_THRESHOLD_SHIFT_SEC = 5


def _pace_sec_per_km(speed_mps: Optional[float]) -> Optional[float]:
    if not speed_mps:
        return None
    return 1000.0 / speed_mps


def _pace(a: dict[str, Any]) -> Optional[float]:
    """Heat-normalised pace where we have it, raw pace otherwise.

    Measuring easy pace on raw pace mixes fitness with weather: the same effort
    reads 28 s/km slower in a monsoon than in February.
    """
    return a.get("normalized_pace_sec") or _pace_sec_per_km(a.get("average_speed"))


def _fmt_pace(sec: Optional[float]) -> Optional[str]:
    if not sec:
        return None
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _recent(acts: list[dict[str, Any]], now: datetime, days: int) -> list[dict[str, Any]]:
    cutoff = now - timedelta(days=days)
    return [a for a in acts
            if a.get("start_date") and a["start_date"] >= cutoff
            and (a.get("distance") or 0) >= MIN_DIST_M]


def _evidence(a: dict[str, Any]) -> dict[str, Any]:
    pace = _pace_sec_per_km(a.get("average_speed"))   # what the watch actually said
    norm = a.get("normalized_pace_sec")
    penalty = a.get("heat_penalty_sec")
    return {
        "activity_id": a.get("id"),
        "date": a["start_date"].date().isoformat() if a.get("start_date") else None,
        "distance_km": round((a.get("distance") or 0) / 1000.0, 2),
        "pace_sec": round(pace) if pace else None,
        "pace": _fmt_pace(pace),
        "avg_hr": round(a["average_heartrate"]) if a.get("average_heartrate") else None,
        # Show the weather we corrected for, so the correction can be checked.
        "temp_c": round(a["temp_c"], 1) if a.get("temp_c") is not None else None,
        "dew_point_c": round(a["dew_point_c"], 1) if a.get("dew_point_c") is not None else None,
        "heat_penalty_sec": round(penalty) if penalty else 0,
        "normalized_pace": _fmt_pace(norm) if norm else None,
    }


def _confidence(n: int) -> str:
    for need, label in CONFIDENCE_BY_N:
        if n >= need:
            return label
    return "none"


def measure_easy_pace(acts: list[dict[str, Any]], easy_hr_ceiling: int,
                      now: datetime) -> dict[str, Any]:
    """Measure easy pace from runs actually run at an easy heart rate.

    This is the honest version of the estimate in fitness_model: if the runner
    has never run easy, we say so instead of inventing a number.
    """
    pool = _recent(acts, now, LOOKBACK_DAYS)
    with_hr = [a for a in pool if a.get("average_heartrate")]
    easy = [a for a in with_hr
            if a["average_heartrate"] <= easy_hr_ceiling + HR_TOLERANCE
            and _pace(a)]

    # Recent runs describe current fitness better than six-week-old ones, but a
    # plain "latest wins" would swing wildly on one odd run. Mean of the newest few.
    easy.sort(key=lambda a: a["start_date"], reverse=True)
    used = easy[:5]
    paces = [_pace(a) for a in used]
    measured = round(sum(paces) / len(paces)) if paces else None

    return {
        "measured_easy_pace_sec": measured,
        "measured_easy_pace": _fmt_pace(measured),
        "method": "measured" if measured else "no_easy_runs",
        "confidence": _confidence(len(used)),
        "easy_hr_ceiling": easy_hr_ceiling,
        "runs_considered": len(pool),
        "runs_with_hr": len(with_hr),
        "easy_runs_found": len(used),
        "evidence": [_evidence(a) for a in used],
        # The runs that were TOO HARD to count. This is the number that explains
        # "why is my plan still slow?" better than anything else we compute.
        "too_hard": [_evidence(a) for a in with_hr
                     if a["average_heartrate"] > easy_hr_ceiling + HR_TOLERANCE][:8],
    }


def historical_easy(acts: list[dict[str, Any]], easy_hr_ceiling: int,
                    now: datetime) -> dict[str, Any]:
    """Easy-HR runs older than the live window but still recent enough to mean
    something. Too stale to set a target; they answer a different question —
    has this runner done it lately?

    Capped at a year on purpose. Reaching further back finds a fitter athlete
    from two seasons ago, and telling someone rebuilding that they "used to run
    5:43/km easy" is neither useful nor kind.
    """
    floor = now - timedelta(days=HISTORICAL_MAX_DAYS)
    old = [a for a in acts
           if a.get("average_heartrate")
           and a["average_heartrate"] <= easy_hr_ceiling + HR_TOLERANCE
           and (a.get("distance") or 0) >= MIN_DIST_M
           and _pace_sec_per_km(a.get("average_speed"))
           and a.get("start_date")
           and floor <= a["start_date"] < now - timedelta(days=LOOKBACK_DAYS)]
    if not old:
        return {"count": 0, "avg_pace": None, "last_date": None, "evidence": []}

    old.sort(key=lambda a: a["start_date"], reverse=True)
    paces = [_pace(a) for a in old]
    avg = round(sum(paces) / len(paces))
    return {
        "count": len(old),
        "avg_pace_sec": avg,
        "avg_pace": _fmt_pace(avg),
        "best_pace": _fmt_pace(round(min(paces))),
        "last_date": old[0]["start_date"].date().isoformat(),
        "evidence": [_evidence(a) for a in old[:5]],
    }


def measure_threshold(acts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Fastest sustained effort in the recent window — our read on top-end fitness."""
    pool = [a for a in _recent(acts, now, LOOKBACK_DAYS)
            if (a.get("distance") or 0) >= MIN_THRESHOLD_DIST_M
            and _pace(a)]
    if not pool:
        return {"threshold_pace_sec": None, "method": "no_qualifying_runs",
                "evidence": [], "runs_considered": 0}

    best = min(pool, key=lambda a: _pace(a))
    pace = round(_pace(best))
    return {
        "threshold_pace_sec": pace,
        "threshold_pace": _fmt_pace(pace),
        "method": "measured",
        "runs_considered": len(pool),
        "evidence": [_evidence(best)],
    }


def _band(easy_pace: int) -> tuple[int, int]:
    """Same band shape the plan generator uses, so calibration stays consistent."""
    return easy_pace - 15, easy_pace + 20


# -- retarget ----------------------------------------------------------------

# Which day types ride on easy pace, and how their band is derived from it.
# Quality/race sit on GOAL pace (the target the runner chose), so calibrating
# fitness must not silently move them — that would be us editing their goal.
_EASY_BASED = {
    "easy":    lambda e: (e - 15, e + 20),
    "strides": lambda e: (e - 15, e + 20),
    "long":    lambda e: (e, e + 30),
}


def retarget_workout(day_type: str, structure: Optional[dict[str, Any]],
                     new_easy_pace: int) -> Optional[dict[str, Any]]:
    """New pace band + rewritten step targets for one workout, or None if this
    day type isn't driven by easy pace."""
    shape = _EASY_BASED.get(day_type)
    if not shape:
        return None
    low, high = shape(new_easy_pace)

    new_structure = None
    if structure:
        new_structure = {**structure}
        steps = []
        for s in (structure.get("steps") or []):
            s = {**s}
            if s.get("target_kind") == "pace":
                s["pace_low_sec"], s["pace_high_sec"] = low, high
            steps.append(s)
        if steps:
            new_structure["steps"] = steps

    return {"pace_low_sec": low, "pace_high_sec": high, "structure": new_structure}


def calibrate(snapshot: dict[str, Any], acts: list[dict[str, Any]],
              now: datetime, current_easy_low: Optional[int] = None) -> dict[str, Any]:
    """Compare the plan's assumptions against reality and propose adjustments.

    Returns the full working — evidence, insights, and the concrete changes —
    so the UI can show WHY, not just WHAT.
    """
    ceiling = snapshot.get("easy_hr_ceiling") or 155
    plan_easy = snapshot.get("easy_pace_sec")
    plan_threshold = snapshot.get("threshold_pace_sec")
    plan_method = snapshot.get("easy_pace_method")

    easy = measure_easy_pace(acts, ceiling, now)
    thr = measure_threshold(acts, now)

    insights: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    proposed: dict[str, Any] = {}

    # --- Easy pace -----------------------------------------------------------
    measured = easy["measured_easy_pace_sec"]
    if measured and easy["confidence"] in ("medium", "high"):
        baseline = current_easy_low + 15 if current_easy_low else plan_easy
        delta = round(baseline - measured) if baseline else 0
        if baseline and abs(delta) >= MEANINGFUL_EASY_SHIFT_SEC:
            low, high = _band(measured)
            proposed["easy_low_sec"], proposed["easy_high_sec"] = low, high
            faster = delta > 0
            changes.append({
                "field": "easy_pace",
                "from": f"{_fmt_pace(baseline - 15)}-{_fmt_pace(baseline + 20)}/km",
                "to": f"{_fmt_pace(low)}-{_fmt_pace(high)}/km",
                "delta_sec": delta,
                "reason": (
                    f"Your last {easy['easy_runs_found']} easy-HR run(s) averaged "
                    f"{easy['measured_easy_pace']}/km at or below {ceiling} bpm — "
                    f"{abs(delta)} s/km {'faster' if faster else 'slower'} than the plan assumed."
                ),
            })
            insights.append({
                "kind": "easy_pace_" + ("faster" if faster else "slower"),
                "title": (f"Your easy pace has improved to {easy['measured_easy_pace']}/km"
                          if faster else
                          f"Your easy pace is really {easy['measured_easy_pace']}/km"),
                "detail": (
                    f"Measured from {easy['easy_runs_found']} run(s) where your average HR "
                    f"stayed at or below your easy ceiling of {ceiling} bpm. The plan was "
                    f"built on {_fmt_pace(baseline)}/km, so the targets move "
                    f"{'faster' if faster else 'slower'} by {abs(delta)} s/km."
                ),
                "evidence": easy["evidence"],
                "confidence": easy["confidence"],
            })
        else:
            insights.append({
                "kind": "easy_pace_on_track",
                "title": "Your easy pace is tracking the plan",
                "detail": (
                    f"Measured {easy['measured_easy_pace']}/km at HR ≤{ceiling} bpm, which is "
                    f"within {MEANINGFUL_EASY_SHIFT_SEC} s/km of what the plan assumed. "
                    "Too small to be fitness rather than noise, so nothing changes."
                ),
                "evidence": easy["evidence"],
                "confidence": easy["confidence"],
            })
    elif measured and easy["confidence"] == "low":
        insights.append({
            "kind": "easy_pace_low_confidence",
            "title": f"Only {easy['easy_runs_found']} easy run to go on",
            "detail": (
                f"That run came out at {easy['measured_easy_pace']}/km at HR ≤{ceiling} bpm, "
                f"which is promising — but one run isn't enough to move your targets on. "
                f"Give me {CONFIDENCE_BY_N[1][0]} and I'll recalibrate."
            ),
            "evidence": easy["evidence"],
            "confidence": "low",
        })
    else:
        # The most important insight this module can produce — and it must not
        # claim the runner has "never" run easy when their history says otherwise.
        hard = easy["too_hard"]
        hist = historical_easy(acts, ceiling, now)
        detail = (
            f"None of your last {easy['runs_with_hr']} run(s) with heart-rate data stayed at or "
            f"below your easy ceiling of {ceiling} bpm"
        )
        if hard:
            hrs = [e["avg_hr"] for e in hard if e["avg_hr"]]
            if hrs:
                detail += f" — they averaged {min(hrs)}–{max(hrs)} bpm"
        detail += "."

        if hist["count"]:
            detail += (
                f" You are not incapable of it, though: in the past year you have {hist['count']} "
                f"run(s) at an easy heart rate, averaging {hist['avg_pace']}/km, the most recent "
                f"on {hist['last_date']}. They sit outside the {LOOKBACK_DAYS}-day window so they "
                f"can't set your current target — but they show this is something you were doing "
                f"before the training gap and the monsoon, not something you've never managed."
            )
        else:
            detail += (
                " Your easy pace has never been measured — it's still the conservative estimate "
                "the plan started with"
                + (f" ({_fmt_pace(plan_easy)}/km)." if plan_easy else ".")
            )
        insights.append({
            "kind": "no_recent_easy_runs",
            "title": ("Your easy pace hasn't been measured recently" if hist["count"]
                      else "Your easy pace can't be measured yet"),
            "detail": detail,
            "evidence": hard,
            "historical": hist,
            "confidence": "none",
        })

    # --- Threshold / top-end fitness ----------------------------------------
    if thr["threshold_pace_sec"] and plan_threshold:
        delta = round(plan_threshold - thr["threshold_pace_sec"])
        # Reported, never auto-applied: quality sessions ride on the GOAL pace the
        # runner chose, so top-end fitness informs them rather than rewriting them.
        if abs(delta) >= MEANINGFUL_THRESHOLD_SHIFT_SEC:
            faster = delta > 0
            insights.append({
                "kind": "threshold_" + ("faster" if faster else "slower"),
                "title": (f"Top-end fitness up {abs(delta)} s/km" if faster
                          else f"Top-end fitness down {abs(delta)} s/km"),
                "detail": (
                    f"Best sustained effort is now {thr['threshold_pace']}/km versus "
                    f"{_fmt_pace(plan_threshold)}/km when the plan was built."
                    + ("" if faster else
                       " That usually means a training gap or accumulated fatigue rather "
                       "than lost ability — it comes back quickly.")
                ),
                "evidence": thr["evidence"],
                "confidence": "medium",
            })

    return {
        "easy": easy,
        "threshold": thr,
        "insights": insights,
        "changes": changes,
        "proposed": proposed,
        "has_changes": bool(changes),
        "plan_assumed": {
            "easy_pace_sec": plan_easy,
            "easy_pace": _fmt_pace(plan_easy),
            "easy_pace_method": plan_method,
            "threshold_pace_sec": plan_threshold,
            "threshold_pace": _fmt_pace(plan_threshold),
            "easy_hr_ceiling": ceiling,
        },
        "window_days": LOOKBACK_DAYS,
    }
