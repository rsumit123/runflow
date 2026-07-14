"""
Read the runner's recovery state and decide whether today's session should stand.

Two honesty rules drive this module:

1. A signal with no baseline is not a signal. Garmin needs weeks to learn an HRV
   baseline; until `status` says so, lastNightAvg is a number without a meaning
   and we refuse to score it.
2. Never silently rewrite the plan. We propose, we explain which factor drove
   the call, and the runner accepts.

Pure: Garmin payloads in, assessment out. No network, no DB.
"""
from __future__ import annotations

from typing import Any, Optional

# Garmin's own readiness score already fuses sleep, HRV, recent load and rest.
# We lean on it as the spine and use the individual factors to EXPLAIN it.
READY_HIGH = 70      # primed — quality sessions are on
READY_MODERATE = 50  # normal — proceed as planned
READY_LOW = 30       # compromised — pull the intensity out
# below READY_LOW — rest or a token shakeout

# Sessions that ask for real intensity. These are what we downgrade.
HARD_DAYS = {"quality", "long", "speed_endurance", "max_velocity", "accel", "test"}

# What a hard day becomes when the runner is under-recovered.
DOWNGRADE = {
    "quality": "easy",
    "long": "easy",
    "speed_endurance": "technique",
    "max_velocity": "technique",
    "accel": "technique",
    "test": "technique",
}

SHORT_SLEEP_H = 6.0
LOW_BODY_BATTERY = 40


def _hrv(hrv: Optional[dict[str, Any]]) -> dict[str, Any]:
    """HRV, but only when Garmin has actually established a baseline for it."""
    s = (hrv or {}).get("hrvSummary") or {}
    last, status = s.get("lastNightAvg"), s.get("status")
    if not last:
        return {"available": False, "reason": "no_reading"}
    # BALANCED / UNBALANCED / LOW mean a baseline exists. NONE means Garmin is
    # still onboarding this metric — the number is real but has nothing to mean.
    if not status or status == "NONE" or s.get("baseline") is None:
        return {"available": False, "reason": "no_baseline", "last_night_ms": last,
                "detail": ("Garmin is still learning your HRV baseline, so last night's "
                           f"{last} ms can't be read as good or bad yet.")}
    return {"available": True, "last_night_ms": last, "status": status,
            "weekly_avg_ms": s.get("weeklyAvg"),
            "good": status == "BALANCED"}


def _sleep_hours(sleep: Optional[dict[str, Any]]) -> Optional[float]:
    secs = ((sleep or {}).get("dailySleepDTO") or {}).get("sleepTimeSeconds")
    return round(secs / 3600.0, 1) if secs else None


def _sleep_score(sleep: Optional[dict[str, Any]], readiness: Optional[dict[str, Any]]) -> Optional[int]:
    dto = (sleep or {}).get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = (scores.get("overall") or {}).get("value")
    return overall or (readiness or {}).get("sleepScore")


def _body_battery(bb: Optional[list[dict[str, Any]]]) -> Optional[int]:
    """Highest Body Battery of the day — i.e. what the runner woke up with."""
    if not bb:
        return None
    vals = [v for row in bb for _, v in (row.get("bodyBatteryValuesArray") or []) if v is not None]
    return max(vals) if vals else None


def _rhr(rhr: Optional[dict[str, Any]]) -> Optional[int]:
    metrics = ((rhr or {}).get("allMetrics") or {}).get("metricsMap") or {}
    rows = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
    if not rows or rows[0].get("value") is None:
        return None
    return int(rows[0]["value"])


def _level(score: Optional[int]) -> str:
    if score is None:
        return "unknown"
    if score >= READY_HIGH:
        return "high"
    if score >= READY_MODERATE:
        return "moderate"
    if score >= READY_LOW:
        return "low"
    return "very_low"


def assess(readiness: Optional[list | dict], hrv: Optional[dict],
           sleep: Optional[dict], body_battery: Optional[list],
           rhr: Optional[dict] = None) -> dict[str, Any]:
    """Fold Garmin's wellness payloads into one readable verdict + its factors."""
    r = readiness[0] if isinstance(readiness, list) and readiness else (readiness or {})
    if not isinstance(r, dict):
        r = {}

    score = r.get("score")
    level = _level(score)

    factors: list[dict[str, Any]] = []

    hours = _sleep_hours(sleep)
    s_score = _sleep_score(sleep, r)
    if hours is not None:
        short = hours < SHORT_SLEEP_H
        factors.append({
            "name": "Sleep",
            "value": f"{hours} h" + (f" · score {s_score}" if s_score else ""),
            "verdict": "bad" if short else "good",
            "detail": ("Short sleep is the single biggest drag on a hard session."
                       if short else "Enough sleep to absorb a hard session."),
        })

    bb = _body_battery(body_battery)
    if bb is not None:
        low = bb < LOW_BODY_BATTERY
        factors.append({
            "name": "Body Battery",
            "value": f"{bb} at its peak",
            "verdict": "bad" if low else "good",
            "detail": ("You never charged up today — your reserves are already low."
                       if low else "You charged up well overnight."),
        })

    h = _hrv(hrv)
    if h["available"]:
        factors.append({
            "name": "HRV",
            "value": f"{h['last_night_ms']} ms · {h['status'].lower()}",
            "verdict": "good" if h["good"] else "bad",
            "detail": ("Overnight HRV sits in your normal range." if h["good"]
                       else "Overnight HRV is outside your normal range — a sign of strain."),
        })
    elif h.get("reason") == "no_baseline":
        factors.append({
            "name": "HRV",
            "value": "no baseline yet",
            "verdict": "unknown",
            "detail": h["detail"],
        })

    resting = _rhr(rhr)
    if resting is not None:
        factors.append({
            "name": "Resting HR",
            "value": f"{resting} bpm",
            "verdict": "neutral",
            "detail": "A jump of 5+ bpm over your normal is an early fatigue flag.",
        })

    return {
        "score": score,
        "level": level,
        "garmin_level": r.get("level"),
        "feedback": r.get("feedbackShort"),
        "factors": factors,
        "available": score is not None,
    }


def adjust(day_type: str, assessment: dict[str, Any]) -> dict[str, Any]:
    """Should today's session stand? Returns the call, the reason, and the swap."""
    level = assessment.get("level")
    score = assessment.get("score")

    if not assessment.get("available"):
        return {"action": "keep", "reason": "No readiness data from your watch today.",
                "confidence": "none"}

    if day_type == "rest":
        return {"action": "keep", "reason": "Rest day — nothing to adjust.",
                "confidence": "high"}

    is_hard = day_type in HARD_DAYS
    drivers = [f["name"] for f in assessment.get("factors", []) if f["verdict"] == "bad"]
    because = (" Driven by: " + ", ".join(drivers).lower() + ".") if drivers else ""

    if level == "very_low":
        return {
            "action": "rest",
            "swap_to": None,
            "reason": (f"Readiness is {score}/100 — your body is not absorbing training right "
                       f"now. Taking today off will make this week better, not worse.{because}"),
            "confidence": "high",
        }

    if level == "low" and is_hard:
        return {
            "action": "downgrade",
            "swap_to": DOWNGRADE.get(day_type, "easy"),
            "reason": (f"Readiness is {score}/100. A hard session today would cost more than it "
                       f"builds — run it easy and keep the quality for when you can do it "
                       f"justice.{because}"),
            "confidence": "high",
        }

    if level == "low":
        return {
            "action": "keep",
            "reason": (f"Readiness is {score}/100, but this is already an easy day — keep it, "
                       f"and stay honestly easy.{because}"),
            "confidence": "medium",
        }

    if level == "high" and is_hard:
        return {
            "action": "keep",
            "reason": f"Readiness is {score}/100 — you're primed. Run the session as written.",
            "confidence": "high",
        }

    return {
        "action": "keep",
        "reason": f"Readiness is {score}/100 — normal. Run the session as written.",
        "confidence": "medium",
    }
