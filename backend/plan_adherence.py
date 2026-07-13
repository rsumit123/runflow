"""
Plan adherence + suggestion engine (v2b).

Matches actual runs to planned workouts, grades whether easy days were run easy,
scores compatibility, and proposes concrete, guardrail-safe tweaks the user can
accept or decline. Pure functions over dicts — no DB, no I/O.

workout dicts: {id, date(datetime), week_number, day_type, target_distance_m,
                pace_low_sec, pace_high_sec, hr_ceiling, title, description}
activity dicts: {id, distance(m), start_date(datetime), average_speed(m/s),
                 average_heartrate}
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

MATCH_WINDOW_DAYS = 1        # a run within ±1 day fulfils a workout
RAN_HARD_MARGIN = 5          # avg HR over the ceiling by more than this = ran hard
EASY_TYPES = {"easy", "long", "strides"}


def _pace_sec(speed_mps: Optional[float]) -> Optional[int]:
    if not speed_mps or speed_mps <= 0:
        return None
    return round(1000.0 / speed_mps)


def match_and_grade(
    workouts: list[dict[str, Any]], activities: list[dict[str, Any]], now: datetime,
    plan_start: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return {workouts: enriched, summary: {...}} — pure, order-preserving.

    Runs from before ``plan_start`` are ignored — a run that happened before the
    plan existed can't fulfil one of its workouts.
    """
    runs = [a for a in activities if a.get("start_date") and a.get("distance")]
    if plan_start is not None:
        runs = [a for a in runs if a["start_date"].date() >= plan_start.date()]
    used: set[Any] = set()
    enriched: list[dict[str, Any]] = []

    done = missed = planned_past = easy_run_hard = easy_graded = 0

    for w in workouts:
        e = dict(w)
        wdate = w.get("date")
        if w.get("day_type") == "rest" or not wdate:
            e.update(status="rest", actual=None, compliance=None)
            enriched.append(e)
            continue

        # find the nearest unused run within the window
        best = None
        best_gap = None
        for a in runs:
            if a["id"] in used:
                continue
            gap = abs((a["start_date"].date() - wdate.date()).days)
            if gap <= MATCH_WINDOW_DAYS and (best_gap is None or gap < best_gap):
                best, best_gap = a, gap

        is_past = wdate.date() < now.date()
        if best is not None:
            used.add(best["id"])
            done += 1
            if is_past:
                planned_past += 1
            actual = {
                "activity_id": best["id"],
                "pace_sec": _pace_sec(best.get("average_speed")),
                "avg_hr": best.get("average_heartrate"),
                "distance_m": best.get("distance"),
            }
            compliance = None
            if w.get("day_type") in EASY_TYPES and w.get("hr_ceiling") and best.get("average_heartrate"):
                easy_graded += 1
                if best["average_heartrate"] > w["hr_ceiling"] + RAN_HARD_MARGIN:
                    compliance = "ran_hard"
                    easy_run_hard += 1
                else:
                    compliance = "on_target"
            e.update(status="done", actual=actual, compliance=compliance)
        elif is_past:
            missed += 1
            planned_past += 1
            e.update(status="missed", actual=None, compliance=None)
        else:
            e.update(status="upcoming", actual=None, compliance=None)
        enriched.append(e)

    adherence_pct = round(100 * done / planned_past) if planned_past else None
    summary = {
        "done": done,
        "missed": missed,
        "planned_past": planned_past,
        "adherence_pct": adherence_pct,
        "easy_graded": easy_graded,
        "easy_run_hard": easy_run_hard,
    }
    return {"workouts": enriched, "summary": summary}


def _fmt_pace(sec: Optional[int]) -> str:
    if not sec:
        return "?"
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def suggest(enriched: list[dict[str, Any]], summary: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """Concrete tweaks derived from adherence. Each carries the exact changes to
    apply. `id` is deterministic so the server can re-derive and apply it."""
    suggestions: list[dict[str, Any]] = []

    longs = [w for w in enriched if w.get("day_type") == "long"]
    upcoming_longs = sorted(
        [w for w in longs if w.get("status") == "upcoming"], key=lambda w: w["date"]
    )
    completed_long_km = [
        (w.get("target_distance_m") or 0) / 1000.0
        for w in longs if w.get("status") == "done"
    ]

    # 1) Missed a long run → cap the next long run's growth.
    missed_long = any(w.get("status") == "missed" for w in longs)
    if missed_long and upcoming_longs:
        nxt = upcoming_longs[0]
        base = max(completed_long_km) if completed_long_km else 3.0
        cap_km = round(base + 1.0, 1)
        cur_km = round((nxt.get("target_distance_m") or 0) / 1000.0, 1)
        if cur_km > cap_km:
            suggestions.append({
                "id": f"cap_long_{nxt['id']}",
                "type": "cap_long_run",
                "title": f"Cap your next long run at {cap_km} km",
                "detail": f"You missed a long run, so jumping to {cur_km} km risks overload. "
                          f"Grow from your last completed long run ({base:.1f} km) instead.",
                "changes": [{"workout_id": nxt["id"], "field": "target_distance_m",
                             "value": round(cap_km * 1000)}],
            })

    # 2) Easy days repeatedly run hard → swap the next speed session for easy.
    if summary.get("easy_run_hard", 0) >= 2:
        upcoming_quality = sorted(
            [w for w in enriched if w.get("day_type") == "quality" and w.get("status") == "upcoming"],
            key=lambda w: w["date"],
        )
        if upcoming_quality:
            q = upcoming_quality[0]
            # reuse an easy workout's targets as the template
            easy_ref = next((w for w in enriched if w.get("day_type") == "easy"), {})
            suggestions.append({
                "id": f"soften_quality_{q['id']}",
                "type": "soften_quality",
                "title": "Swap your next speed session for an easy run",
                "detail": "Your easy days keep coming back too hard. Trade the next hard "
                          "session for genuinely easy running until your easy discipline settles.",
                "changes": [
                    {"workout_id": q["id"], "field": "day_type", "value": "easy"},
                    {"workout_id": q["id"], "field": "target_distance_m", "value": 3500},
                    {"workout_id": q["id"], "field": "pace_low_sec", "value": easy_ref.get("pace_low_sec")},
                    {"workout_id": q["id"], "field": "pace_high_sec", "value": easy_ref.get("pace_high_sec")},
                    {"workout_id": q["id"], "field": "hr_ceiling", "value": easy_ref.get("hr_ceiling")},
                    {"workout_id": q["id"], "field": "title", "value": "Easy run (swapped from speed)"},
                    {"workout_id": q["id"], "field": "description",
                     "value": "Rebuilding easy discipline — keep it conversational, under the HR ceiling."},
                ],
            })

    # 3) On track → positive note, no changes.
    if (summary.get("planned_past") or 0) >= 2 and not suggestions \
            and summary.get("adherence_pct", 0) and summary["adherence_pct"] >= 80:
        suggestions.append({
            "id": "on_track",
            "type": "on_track",
            "title": "You're on track — nice work",
            "detail": f"{summary['adherence_pct']}% of planned sessions done and your easy days "
                      "are honest. Keep it rolling; no changes needed.",
            "changes": [],
        })

    return suggestions
