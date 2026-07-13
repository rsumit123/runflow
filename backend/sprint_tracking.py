"""
Sprint plan tracking (S2).

Auto-matches interval-detected activities to planned sprint sessions and derives
100m progress. Sibling to ``plan_adherence.match_and_grade`` (the 5K engine): same
nearest-unused-run-within-a-window matching, same pre-plan-start guard. Pure
functions over dicts — no DB, no I/O.

workout dicts: {id, date(datetime), week_number, day_type, title, ...}
  day_type in {accel, max_velocity, speed_endurance, technique, plyometrics, test, rest}
interval_activity dicts: {id, start_date(datetime), best_100m_sec(float|None),
                          fade_pct(float|None), fastest_rep_sec(float|None)}
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

MATCH_WINDOW_DAYS = 2  # an interval session within ±2 days fulfils a workout
# Only these day types can claim an interval activity. plyometrics/rest never match.
MATCHABLE_TYPES = {"accel", "max_velocity", "speed_endurance", "technique", "test"}


def match_sprint_sessions(
    workouts: list[dict[str, Any]],
    interval_activities: list[dict[str, Any]],
    now: datetime,
    plan_start: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return {workouts: enriched, progress: {...}} — pure, order-preserving.

    Each matchable workout (day_type in MATCHABLE_TYPES), in date order, claims the
    nearest unused interval activity within ±2 days whose start_date is on/after
    ``plan_start`` (sessions from before the plan existed can't fulfil it).
    """
    acts = [a for a in interval_activities if a.get("start_date")]
    if plan_start is not None:
        acts = [a for a in acts if a["start_date"].date() >= plan_start.date()]

    used: set[Any] = set()
    matched_acts: list[dict[str, Any]] = []  # activities claimed by a workout, in claim order
    enriched: list[dict[str, Any]] = []

    done = 0
    planned_past = 0

    for w in sorted(workouts, key=lambda x: x.get("date") or datetime.max):
        e = dict(w)
        wdate = w.get("date")
        day_type = w.get("day_type")

        if day_type == "rest" or not wdate:
            e.update(status="rest", actual=None)
            enriched.append(e)
            continue

        is_past = wdate.date() < now.date()
        matchable = day_type in MATCHABLE_TYPES

        best = None
        best_gap = None
        if matchable:
            for a in acts:
                if a["id"] in used:
                    continue
                gap = abs((a["start_date"].date() - wdate.date()).days)
                if gap <= MATCH_WINDOW_DAYS and (best_gap is None or gap < best_gap):
                    best, best_gap = a, gap

        if best is not None:
            used.add(best["id"])
            matched_acts.append(best)
            done += 1
            planned_past += 1  # a matched session is, by construction, in the past
            e.update(status="done", actual={
                "activity_id": best["id"],
                "best_100m_sec": best.get("best_100m_sec"),
                "fade_pct": best.get("fade_pct"),
                "fastest_rep_sec": best.get("fastest_rep_sec"),
            })
        elif is_past:
            if matchable:
                planned_past += 1
            e.update(status="missed", actual=None)
        else:
            e.update(status="upcoming", actual=None)
        enriched.append(e)

    # Matching runs in date order; re-key enriched back to the caller's order by id.
    by_wid = {w.get("id"): e for w, e in zip(sorted(workouts, key=lambda x: x.get("date") or datetime.max), enriched)}
    enriched = [by_wid[w.get("id")] for w in workouts]

    adherence_pct = round(100 * done / planned_past) if planned_past else None

    # Progress derived from matched activities that carry a best_100m_sec.
    with_best = [a for a in matched_acts if a.get("best_100m_sec") is not None]
    trend_sorted = sorted(with_best, key=lambda a: a["start_date"])
    best_100m_trend = [
        {"date": a["start_date"].date().isoformat(), "sec": a["best_100m_sec"]}
        for a in trend_sorted
    ]
    latest_best_100m_sec = trend_sorted[-1]["best_100m_sec"] if trend_sorted else None

    progress = {
        "sessions_done": done,
        "sessions_planned_past": planned_past,
        "adherence_pct": adherence_pct,
        "latest_best_100m_sec": latest_best_100m_sec,
        "best_100m_trend": best_100m_trend,
    }
    return {"workouts": enriched, "progress": progress}
