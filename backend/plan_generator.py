"""
Deterministic 5K plan generator (v2a).

Builds a week-by-week plan from the runner's fitness model + chosen target,
where every workout is guardrail-compliant BY CONSTRUCTION (80/20 easy, long run
grows ≤1 km/week, down week every 4th, quality only once base supports it, taper
into race). No LLM here — the LLM only phrases the result later. Pure functions.

Weekly template (Mon=0): Mon easy, Wed quality, Thu easy, Sat long; rest otherwise.
Down weeks drop the quality + 2nd easy and shorten the long run.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

QUALITY_START_WEEK = 3       # no hard sessions until an easy base is laid
LONG_RUN_CAP_KM = 5.0        # a 5K plan's long run needn't exceed race distance + buffer
LONG_RUN_START_CAP_KM = 4.0  # don't start the long run too high
DOWN_WEEK_FACTOR = 0.7

# Every steady run is bookended by an easy warm-up and cool-down (Garmin-Coach
# style). Quality/race sessions carry their own warm-up in the description.
WARMUP_5K = "2 min easy jog + a few leg swings to open the legs up."
COOLDOWN_5K = "2 min easy jog, then light stretching."
RUN_STRUCTURE = {"warmup": WARMUP_5K, "cooldown": COOLDOWN_5K}


def _wo(week_monday: datetime, weekday: int, week_num: int, day_type: str,
        dist_km: Optional[float], pace_low: Optional[int], pace_high: Optional[int],
        hr_ceiling: Optional[int], title: str, desc: str,
        structure: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "date": week_monday + timedelta(days=weekday),
        "week_number": week_num,
        "day_type": day_type,
        "target_distance_m": round(dist_km * 1000) if dist_km else None,
        "pace_low_sec": pace_low,
        "pace_high_sec": pace_high,
        "hr_ceiling": hr_ceiling,
        "title": title,
        "description": desc,
        "structure": structure,
    }


def generate_plan(model: dict[str, Any], weeks: int, target_time_sec: int,
                  start_date: datetime) -> dict[str, Any]:
    easy_pace = model.get("easy_pace_sec") or 450          # sec/km
    ceiling = model.get("easy_hr_ceiling") or 155
    threshold = model.get("threshold_pace_sec") or round(target_time_sec / 5.0)
    goal_pace = round(target_time_sec / 5.0)               # 5K goal pace sec/km

    base_long = min(LONG_RUN_START_CAP_KM, max(3.0, model.get("longest_run_28d_km") or 3.0))

    easy_low, easy_high = easy_pace - 15, easy_pace + 20
    long_low, long_high = easy_pace, easy_pace + 30        # long runs a touch easier

    # Anchor weeks to the Monday of the plan's first week.
    week0_monday = start_date - timedelta(days=start_date.weekday())

    workouts: list[dict[str, Any]] = []
    for w in range(1, weeks + 1):
        wk_mon = week0_monday + timedelta(days=7 * (w - 1))
        is_taper = (w == weeks)
        is_down = (w % 4 == 0) and not is_taper

        if is_taper:
            workouts.append(_wo(wk_mon, 0, w, "easy", 3.0, easy_low, easy_high, ceiling,
                                "Easy shakeout", "Very easy, legs-only. Stay under the HR ceiling.", RUN_STRUCTURE))
            workouts.append(_wo(wk_mon, 3, w, "strides", 3.0, easy_low, easy_high, ceiling,
                                "Easy + strides", "2 km easy, then 4 × 20 s strides at fast-but-relaxed effort, full walk-back recovery.", RUN_STRUCTURE))
            workouts.append(_wo(wk_mon, 5, w, "quality", 5.0, goal_pace, goal_pace, None,
                                "Race day — 5K", f"Goal: {_fmt(target_time_sec)}. Even pacing ~{_fmt_pace(goal_pace)}/km. Start controlled."))
            continue

        long_km = min(LONG_RUN_CAP_KM, base_long + (w - 1) * 1.0)
        if is_down:
            long_km = round(long_km * DOWN_WEEK_FACTOR, 1)

        # Easy runs must never out-distance the week's long run. In early weeks the
        # base is tiny, so cap the easy runs just below the long run (min 2 km floor)
        # to keep the long run the clear longest session of the week.
        easy_cap = max(2.0, round(long_km - 0.5, 1))
        easy_short_km = min(3.0, easy_cap)
        easy_long_km = min(3.5, easy_cap)

        # Mon — easy (always)
        workouts.append(_wo(wk_mon, 0, w, "easy", easy_short_km, easy_low, easy_high, ceiling,
                            "Easy run", f"Conversational, nose-breathing. Keep HR ≤{ceiling} bpm — if it climbs, slow down.", RUN_STRUCTURE))
        # Wed — quality (once base supports it, not on down weeks)
        if not is_down and w >= QUALITY_START_WEEK:
            workouts.append(_wo(wk_mon, 2, w, "quality", min(4.0, long_km), goal_pace - 10, goal_pace + 5, None,
                                "Speed session", f"1.5 km easy warm-up, 6 × 400 m at ~{_fmt_pace(goal_pace)}/km (5K effort) with 90 s jog recovery, easy cool-down."))
        # Thu — easy (not on down weeks)
        if not is_down:
            workouts.append(_wo(wk_mon, 3, w, "easy", easy_long_km, easy_low, easy_high, ceiling,
                                "Easy run", f"Easy aerobic. HR ≤{ceiling} bpm.", RUN_STRUCTURE))
        # Sat — long
        workouts.append(_wo(wk_mon, 5, w, "long", long_km, long_low, long_high, ceiling + 5,
                            f"Long run — {long_km} km",
                            f"Steady and easy{' (down week — shorter)' if is_down else ''}. Build endurance, not speed.",
                            RUN_STRUCTURE))

    goal_date = week0_monday + timedelta(days=7 * (weeks - 1) + 5)  # taper-week Saturday
    return {"goal_date": goal_date, "workouts": workouts, "goal_pace_sec": goal_pace}


def _fmt(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _fmt_pace(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"
