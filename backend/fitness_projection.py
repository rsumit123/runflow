"""
5K target projection (v2a).

Estimates the runner's *current* 5K time from their best recent sustained effort
(Riegel formula), then projects realistic targets at fixed horizons using a
capped, returning-runner improvement rate. Pure functions — unit-testable.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

RIEGEL_EXP = 1.06            # standard endurance fatigue exponent
IMPROVE_PER_WEEK = 0.006     # ~0.6%/week race-time improvement (80/20, returning)
MAX_TOTAL_IMPROVE = 0.10     # never project more than 10% total improvement
HORIZONS = [8, 10, 12]       # weeks


def _riegel(t1_sec: float, d1_m: float, d2_m: float) -> float:
    return t1_sec * (d2_m / d1_m) ** RIEGEL_EXP


def estimate_current_5k(acts: list[dict[str, Any]], now: datetime) -> Optional[dict[str, Any]]:
    """Estimate current 5K time from the fastest recent sustained run."""
    def candidates(min_dist: float, since_days: Optional[int]):
        out = []
        for a in acts:
            if (a.get("distance") or 0) < min_dist or not a.get("average_speed"):
                continue
            if since_days is not None and a.get("start_date"):
                if a["start_date"] < now - timedelta(days=since_days):
                    continue
            out.append(a)
        return out

    pool = candidates(2000, 90) or candidates(1500, 365) or candidates(1200, None)
    if not pool:
        return None

    best = max(pool, key=lambda a: a["average_speed"])  # fastest avg pace
    d1 = best["distance"]
    t1 = d1 / best["average_speed"]
    return {
        "current_5k_sec": round(_riegel(t1, d1, 5000)),
        "from_distance_m": round(d1),
        "from_run_id": best.get("id"),
        "confidence": "ok" if d1 >= 3000 else "low",
    }


def projections(acts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    cur = estimate_current_5k(acts, now)
    if cur is None:
        return {"current_5k_sec": None, "horizons": [], "confidence": "none"}
    c = cur["current_5k_sec"]
    horizons = []
    for w in HORIZONS:
        imp = min(IMPROVE_PER_WEEK * w, MAX_TOTAL_IMPROVE)
        horizons.append({
            "weeks": w,
            "target_time_sec": round(c * (1 - imp)),
            "improvement_pct": round(imp * 100, 1),
        })
    return {
        "current_5k_sec": c,
        "from_distance_m": cur["from_distance_m"],
        "confidence": cur["confidence"],
        "horizons": horizons,
    }


def reality_check(acts: list[dict[str, Any]], now: datetime,
                  target_time_sec: int, weeks: int) -> dict[str, Any]:
    """Is a user-chosen target trainable in `weeks`? (F6 sanity check.)"""
    cur = estimate_current_5k(acts, now)
    if cur is None:
        return {"realistic": None, "current_5k_sec": None, "trainable_target_sec": None}
    imp = min(IMPROVE_PER_WEEK * weeks, MAX_TOTAL_IMPROVE)
    trainable = round(cur["current_5k_sec"] * (1 - imp))
    return {
        "realistic": target_time_sec >= trainable,  # slower-or-equal = achievable
        "current_5k_sec": cur["current_5k_sec"],
        "trainable_target_sec": trainable,
    }
