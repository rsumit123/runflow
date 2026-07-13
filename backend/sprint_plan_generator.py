"""
Deterministic 100m sprint plan generator.

Sibling to the 5K plan_generator: builds a week-by-week sprint plan where every
session is guardrail-compliant BY CONSTRUCTION. No LLM here — the LLM only phrases
the result later. Pure functions.

Guardrails baked in:
- `days` spacing (Mon/Wed/Sat) gives >=48h between CNS-max sessions.
- Full prescribed recovery per session type (walk-back, 8-10 min for max-velocity,
  ~1 min per 10m for speed-endurance).
- Volume ramped conservatively; deload every 4th week (~-40%, 2 sessions).
- Plyo foot-contacts ramp ~40 -> 100 across the block.
- effort_pct capped at 100; max-velocity builds to 100% only after week 2.

Weekly template (non-deload, non-taper): day0 accel (+ short plyo), day1 max_velocity
(+ technique drills), day2 speed_endurance (the emphasis session for a
speed-endurance diagnosis). Week 1 is a foundation week that opens with a baseline
`test`; the final (taper) week ends with the goal `test`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

WARMUP = "600 m easy jog + dynamic drills (leg swings, A-skips, B-skips) + 3 build-up runs"


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _rep(reps: int, distance_m: int, effort_pct: int, recovery: str,
         note: Optional[str] = None) -> dict[str, Any]:
    return {
        "reps": reps,
        "distance_m": distance_m,
        "effort_pct": effort_pct,
        "recovery": recovery,
        "note": note,
    }


def _struct(main_set: list[dict[str, Any]], cues: list[str],
            finisher: Optional[str] = None) -> dict[str, Any]:
    total = sum(m["reps"] * m["distance_m"] for m in main_set)
    return {
        "warmup": WARMUP,
        "main_set": main_set,
        "finisher": finisher,
        "cues": cues,
        "total_volume_m": total,
    }


def _wo(week_monday: datetime, weekday: int, week_num: int, day_type: str,
        title: str, desc: str, structure: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": week_monday + timedelta(days=weekday),
        "week_number": week_num,
        "day_type": day_type,
        "title": title,
        "description": desc,
        "structure": structure,
        "target_distance_m": None,
        "pace_low_sec": None,
        "pace_high_sec": None,
        "hr_ceiling": None,
    }


def _plyo_contacts(w: int) -> int:
    return _clamp(40 + (w - 1) * 10, 40, 100)


# --- session builders: each returns (title, description, structure) ---

def _accel(w: int, intro: bool = False, deload: bool = False) -> tuple[str, str, dict[str, Any]]:
    reps = _clamp(4 + (w - 1) // 2, 4, 8)
    dist = _clamp(20 + (w - 1) * 4, 20, 40)
    effort = _clamp(90 + (w - 1) * 2, 90, 100)
    if intro:
        effort = 90
        reps = min(reps, 5)
    if deload:
        reps = max(3, reps - 2)
        effort = max(88, effort - 5)
    contacts = _plyo_contacts(w)
    rep = _rep(reps, dist, effort, "walk back to start (full recovery, ~2-3 min)",
               "Drive phase: push, don't reach — stay low and rise gradually.")
    cues = [
        "tall spine, powerful arm drive",
        "push the ground back and down",
        "gradual rise — don't pop upright early",
    ]
    finisher = (f"Short plyometrics: ~{contacts} foot-contacts (pogo hops + short bounds), "
                "full recovery between sets.")
    tag = " (intro)" if intro else (" (deload)" if deload else "")
    title = f"Acceleration — {reps}×{dist}m{tag}"
    desc = (f"{reps}×{dist}m accelerations at {effort}% from a 2-point start, walk-back "
            f"recovery. Finish with ~{contacts} plyometric foot-contacts.")
    return title, desc, _struct([rep], cues, finisher)


def _max_velocity(w: int, deload: bool = False) -> tuple[str, str, dict[str, Any]]:
    reps = _clamp(4 + (w - 2) // 2, 4, 6)
    dist = _clamp(20 + (w - 2) * 2, 20, 30)
    effort = _clamp(95 + (w - 2) * 2, 95, 100)
    if w <= 2:
        effort = 95  # never full-out top speed before the base is laid
    if deload:
        reps = max(3, reps - 1)
        effort = max(93, effort - 3)
    rep = _rep(reps, dist, effort, "8-10 min between reps (full CNS recovery)",
               "Fly zone: 30m run-in build, then hold max speed through the zone.")
    cues = [
        "relax face, shoulders and hands",
        "tall posture, quick ground contacts",
        "float at top speed — don't strain",
    ]
    finisher = "Technique drills: 3× (A-skip, B-skip, straight-leg dribble) over 20m."
    tag = " (deload)" if deload else ""
    title = f"Max velocity — {reps}×{dist}m flying{tag}"
    desc = (f"{reps} flying {dist}m sprints at {effort}% with full 8-10 min recovery. "
            "Pure top-speed work, plus technique drills.")
    return title, desc, _struct([rep], cues, finisher)


def _speed_endurance(w: int, diagnosis: Optional[str]) -> tuple[str, str, dict[str, Any]]:
    reps = _clamp(4 + (w - 2) // 2, 4, 6)
    dist = _clamp(80 + (w - 2) * 15, 80, 150)
    effort = _clamp(90 + (w - 2), 90, 95)
    emphasis = (diagnosis == "speed_endurance")
    if emphasis:
        reps = min(6, reps + 1)
        dist = min(150, dist + 10)
    rep = _rep(reps, dist, effort, "full recovery ~1 min per 10m of the rep",
               "Even effort — the point is holding speed as fatigue builds, not a fast start.")
    cues = [
        "hold form as you fatigue — that's the whole point",
        "keep arm drive strong when the legs tire",
        "stay tall, don't collapse at the hips",
    ]
    tail = ("Primary emphasis — your late-race fade is the lever."
            if emphasis else "Extend how long you hold top speed.")
    title = f"Speed endurance — {reps}×{dist}m"
    desc = f"{reps}×{dist}m at {effort}% with long recovery. {tail}"
    return title, desc, _struct([rep], cues, finisher=None)


def _technique(w: int, taper: bool = False) -> tuple[str, str, dict[str, Any]]:
    if taper:
        rep = _rep(4, 20, 90, "walk back to start (~2 min)",
                   "Short and sharp — prime the CNS, stay fresh.")
        cues = [
            "tall posture, drive arms, relax face/shoulders",
            "quick feet, push don't reach",
            "smooth and relaxed — save it for the test",
        ]
        finisher = "4× 20m strides at 90%, relaxed and smooth, full walk-back."
        title = "Primer — technique + strides"
        desc = ("Short, sharp session: 4×20m drills into 4×20m strides. "
                "Prime the nervous system, arrive fresh for the test.")
        return title, desc, _struct([rep], cues, finisher)
    rep = _rep(6, 30, 85, "walk back to start (~2 min)",
               "Mechanics over speed — nail posture and rhythm first.")
    cues = [
        "tall posture, drive arms, relax face/shoulders",
        "quick feet, push don't reach",
        "smooth rhythm, no straining",
    ]
    finisher = "Static stretch + hip mobility, ~10 min."
    title = "Technique — 6×30m drill-runs"
    desc = ("6×30m drill-to-run reps at 85%, walk-back recovery. "
            "Grooving mechanics before the speed work ramps.")
    return title, desc, _struct([rep], cues, finisher)


def _test_baseline() -> tuple[str, str, dict[str, Any]]:
    main = [
        _rep(1, 100, 100, "full recovery, walk back (~6-8 min)",
             "Timed 100m from a standing start — record your time."),
        _rep(1, 20, 100, "full recovery",
             "Flying-20m: 30m run-in, time only the 20m for top speed. Record it."),
    ]
    cues = [
        "tall posture, drive arms, relax face/shoulders",
        "run through the line, don't lean early",
    ]
    finisher = "Easy jog cool-down, ~8 min."
    title = "Baseline test — 100m + flying 20m"
    desc = ("Timed 100m from standing + a flying-20m for top speed. "
            "Record both times — they anchor the whole plan.")
    return title, desc, _struct(main, cues, finisher)


def _test_goal(target_100m_sec: float) -> tuple[str, str, dict[str, Any]]:
    main = [
        _rep(1, 100, 100, "full recovery — this is the one rep that matters",
             "All-out 100m. Run relaxed and fast — chase the target."),
    ]
    cues = [
        "tall posture, drive arms, relax face/shoulders",
        "run through the line, don't lean early",
        "trust your speed — stay relaxed",
    ]
    finisher = "Easy jog cool-down, ~10 min."
    title = "Time trial — 100m"
    desc = (f"Time trial. Goal: {float(target_100m_sec):.1f}s. "
            "Full warm-up, then one all-out 100m at full effort.")
    return title, desc, _struct(main, cues, finisher)


def generate_sprint_plan(profile: dict[str, Any], weeks: int, target_100m_sec: float,
                         start_date: datetime,
                         days: tuple[int, int, int] = (0, 2, 5)) -> dict[str, Any]:
    profile = profile or {}
    diagnosis = profile.get("diagnosis")
    prior = bool(profile.get("prior_speed_exposure"))
    d0, d1, d2 = days

    # Anchor weeks to the Monday of the plan's first week (like the 5K generator).
    week0_monday = start_date - timedelta(days=start_date.weekday())

    workouts: list[dict[str, Any]] = []
    for w in range(1, weeks + 1):
        wk_mon = week0_monday + timedelta(days=7 * (w - 1))
        is_taper = (w == weeks)
        is_deload = (w % 4 == 0) and not is_taper

        if is_taper:
            # Short technique+strides primer, then the goal time trial. No day1.
            workouts.append(_wo(wk_mon, d0, w, "technique", *_technique(w, taper=True)))
            workouts.append(_wo(wk_mon, d2, w, "test", *_test_goal(target_100m_sec)))
            continue

        if w == 1:
            # Foundation: baseline test, technique, accel intro.
            workouts.append(_wo(wk_mon, d0, w, "test", *_test_baseline()))
            workouts.append(_wo(wk_mon, d1, w, "technique", *_technique(w)))
            workouts.append(_wo(wk_mon, d2, w, "accel", *_accel(w, intro=True)))
            continue

        if w == 2 and not prior:
            # No prior speed exposure: stay foundation-ish — technique + accel, no max-velocity.
            workouts.append(_wo(wk_mon, d0, w, "accel", *_accel(w)))
            workouts.append(_wo(wk_mon, d1, w, "technique", *_technique(w)))
            workouts.append(_wo(wk_mon, d2, w, "accel", *_accel(w)))
            continue

        if is_deload:
            # Drop to 2 sessions, no speed-endurance, lower effort.
            workouts.append(_wo(wk_mon, d0, w, "accel", *_accel(w, deload=True)))
            workouts.append(_wo(wk_mon, d1, w, "max_velocity", *_max_velocity(w, deload=True)))
            continue

        # Development week: full accel / max_velocity / speed_endurance.
        workouts.append(_wo(wk_mon, d0, w, "accel", *_accel(w)))
        workouts.append(_wo(wk_mon, d1, w, "max_velocity", *_max_velocity(w)))
        workouts.append(_wo(wk_mon, d2, w, "speed_endurance", *_speed_endurance(w, diagnosis)))

    goal_date = workouts[-1]["date"]
    return {
        "goal_date": goal_date,
        "goal_100m_sec": float(target_100m_sec),
        "workouts": workouts,
    }
