"""
Sprint baseline + diagnosis (100m Sprint Plan Engine, S1).

Pure computation over the runner's own sprint history. No DB / no I/O — callers
pass the extracted best efforts and interval-session configs plus a reference
`now`, so this is fully unit-testable (mirrors `fitness_model.py`).

Inputs:
- `best_efforts`:     [{"distance_target": int, "time_seconds": float, "start_date": datetime}]
                      Extracted short-distance PB efforts (100m, 200m, ...).
- `interval_configs`: [{"start_date": datetime, "config": dict}]
                      `Activity.interval_config` for interval sessions. Each config has
                      config["result"]["summary"] (fastest_rep_pace, slowest_rep_pace,
                      total_reps) and config["result"]["segments"] (list; type=="rep"
                      entries carry duration_s).

The output `profile` grounds the diagnosis in the athlete's real numbers: best
100m/200m, average top speed, per-session fade and rep decay, and prior speed
exposure — from which a single `diagnosis` drives the plan's emphasis.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

RECENCY_DAYS = 365          # window for "recent" best efforts / speed exposure
MIN_REPS = 3                # a session must have >=3 reps to be usable
FADE_ENDURANCE_PCT = 20.0   # fade at/above this => speed-endurance limited
TOP_SPEED_FLOOR_MPS = 5.2   # avg top speed below this => top-speed limited


def _best_effort(
    best_efforts: list[dict[str, Any]], distance: int, now: datetime
) -> Optional[dict[str, Any]]:
    """Fastest effort at `distance`: prefer within last 365d, else all-time."""
    at_dist = [e for e in best_efforts if e.get("distance_target") == distance]
    if not at_dist:
        return None
    cutoff = now - timedelta(days=RECENCY_DAYS)
    recent = [e for e in at_dist if e.get("start_date") and e["start_date"] >= cutoff]
    pool = recent or at_dist
    return min(pool, key=lambda e: e["time_seconds"])


def _slope_sec_per_rep(durations: list[float]) -> Optional[float]:
    """Simple least-squares slope of rep duration vs rep index (sec per rep)."""
    n = len(durations)
    if n < 2:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(durations) / n
    num = sum((i - mean_x) * (d - mean_y) for i, d in enumerate(durations))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den == 0:
        return None
    return num / den


def _usable_session(cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return the summary dict if the session has >=3 reps, else None."""
    summary = (((cfg or {}).get("config") or {}).get("result") or {}).get("summary") or {}
    if (summary.get("total_reps") or 0) < MIN_REPS:
        return None
    return summary


def _rep_durations(cfg: dict[str, Any]) -> list[float]:
    segments = (((cfg or {}).get("config") or {}).get("result") or {}).get("segments") or []
    return [
        s["duration_s"]
        for s in segments
        if s.get("type") == "rep" and s.get("duration_s") is not None
    ]


def _supporting_efforts(
    best_efforts: list[dict[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    """Up to the 2 fastest efforts at each of 100m and 200m, for display."""
    out: list[dict[str, Any]] = []
    for distance in (100, 200):
        at_dist = sorted(
            (e for e in best_efforts if e.get("distance_target") == distance),
            key=lambda e: e["time_seconds"],
        )
        for e in at_dist[:2]:
            date = e["start_date"].date().isoformat() if e.get("start_date") else None
            out.append(
                {
                    "distance_m": distance,
                    "time_sec": e["time_seconds"],
                    "date": date,
                }
            )
    out.sort(key=lambda e: (e["distance_m"], e["time_sec"]))
    return out


def build_sprint_profile(
    best_efforts: list[dict[str, Any]],
    interval_configs: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Build the data-grounded sprint profile + diagnosis (see module docstring)."""
    best_100 = _best_effort(best_efforts, 100, now)
    best_200 = _best_effort(best_efforts, 200, now)

    best_100m_sec = best_100["time_seconds"] if best_100 else None
    best_100m_date = (
        best_100["start_date"].date().isoformat()
        if best_100 and best_100.get("start_date")
        else None
    )
    best_200m_sec = best_200["time_seconds"] if best_200 else None

    top_speed_mps = round(100 / best_100m_sec, 2) if best_100m_sec else None

    # Interval sessions: only those with >=3 reps count.
    fades: list[float] = []
    decays: list[float] = []
    interval_sessions = 0
    for cfg in interval_configs:
        summary = _usable_session(cfg)
        if summary is None:
            continue
        interval_sessions += 1
        fastest = summary.get("fastest_rep_pace")
        slowest = summary.get("slowest_rep_pace")
        if fastest and slowest:
            fades.append((slowest / fastest - 1) * 100)
        slope = _slope_sec_per_rep(_rep_durations(cfg))
        if slope is not None:
            decays.append(slope)

    fade_pct = round(sum(fades) / len(fades), 1) if fades else None
    decay_sec_per_rep = round(sum(decays) / len(decays), 1) if decays else None

    cutoff = now - timedelta(days=RECENCY_DAYS)
    recent_intervals = sum(
        1
        for cfg in interval_configs
        if cfg.get("start_date") and cfg["start_date"] >= cutoff
    )
    prior_speed_exposure = recent_intervals >= 2

    diagnosis, diagnosis_detail = _diagnose(
        best_100m_sec, interval_sessions, fade_pct, top_speed_mps, decay_sec_per_rep
    )

    return {
        "best_100m_sec": best_100m_sec,
        "best_100m_date": best_100m_date,
        "best_200m_sec": best_200m_sec,
        "top_speed_mps": top_speed_mps,
        "interval_sessions": interval_sessions,
        "fade_pct": fade_pct,
        "decay_sec_per_rep": decay_sec_per_rep,
        "prior_speed_exposure": prior_speed_exposure,
        "diagnosis": diagnosis,
        "diagnosis_detail": diagnosis_detail,
        "supporting_efforts": _supporting_efforts(best_efforts, now),
    }


def _diagnose(
    best_100m_sec: Optional[float],
    interval_sessions: int,
    fade_pct: Optional[float],
    top_speed_mps: Optional[float],
    decay_sec_per_rep: Optional[float],
) -> tuple[str, str]:
    """Return (diagnosis, human sentence referencing the actual numbers)."""
    if best_100m_sec is None and interval_sessions == 0:
        return (
            "insufficient_data",
            "Not enough sprint history yet — run a timed 100m or a few interval "
            "sessions and we'll build your baseline from real numbers.",
        )

    if fade_pct is not None and fade_pct >= FADE_ENDURANCE_PCT:
        decay_bit = (
            f" (~{decay_sec_per_rep}s slower per rep)"
            if decay_sec_per_rep is not None
            else ""
        )
        return (
            "speed_endurance",
            f"You fade about {fade_pct}% from your fastest to slowest rep{decay_bit} — "
            "you start fast but lose it late, so speed endurance is the lever.",
        )

    if top_speed_mps is not None and top_speed_mps < TOP_SPEED_FLOOR_MPS:
        return (
            "top_speed",
            f"Your average top speed is about {top_speed_mps} m/s over 100m "
            f"(best {best_100m_sec}s) — raising raw top-end speed is the priority.",
        )

    detail_speed = (
        f"top speed ~{top_speed_mps} m/s" if top_speed_mps is not None else "solid speed"
    )
    detail_fade = f", fade only ~{fade_pct}%" if fade_pct is not None else ""
    return (
        "balanced",
        f"Your sprint profile looks balanced ({detail_speed}{detail_fade}) — "
        "we'll develop speed and speed endurance together.",
    )
