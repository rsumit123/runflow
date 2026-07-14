"""
Convert RunFlow's structured workout steps into a Garmin Connect running workout.

Our canonical step shape (stored in PlannedWorkout.structure["steps"]):

    {"type": "warmup"|"run"|"recovery"|"cooldown",
     "end_kind": "time"|"distance", "end_value": <secs or metres>,
     "target_kind": "pace"|"none",
     "pace_low_sec": <sec/km, faster bound>, "pace_high_sec": <sec/km, slower bound>,
     "note": str|None}

    {"type": "repeat", "iterations": N, "steps": [<step>, ...]}

Garmin pace targets are SPEEDS in m/s: targetValueOne = slowest allowed,
targetValueTwo = fastest allowed. Pure conversion — no network here.
"""
from __future__ import annotations

from typing import Any

from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RunningWorkout,
    StepType,
    TargetType,
    WorkoutSegment,
    create_repeat_group,
)

DEFAULT_PACE_SEC = 360  # 6:00/km — only used to estimate duration of distance steps

_STEP_TYPES = {
    "warmup": (StepType.WARMUP, "warmup", 1),
    "cooldown": (StepType.COOLDOWN, "cooldown", 2),
    "run": (StepType.INTERVAL, "interval", 3),
    "recovery": (StepType.RECOVERY, "recovery", 4),
}


def _end_condition(kind: str) -> dict[str, Any]:
    if kind == "distance":
        return {"conditionTypeId": ConditionType.DISTANCE, "conditionTypeKey": "distance",
                "displayOrder": 3, "displayable": True}
    return {"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time",
            "displayOrder": 2, "displayable": True}


def _target(step: dict[str, Any]) -> tuple[dict[str, Any], float | None, float | None]:
    low, high = step.get("pace_low_sec"), step.get("pace_high_sec")
    if step.get("target_kind") == "pace" and low and high:
        return (
            {"workoutTargetTypeId": TargetType.PACE_ZONE, "workoutTargetTypeKey": "pace.zone",
             "displayOrder": 1},
            round(1000.0 / high, 3),   # slower bound -> minimum speed
            round(1000.0 / low, 3),    # faster bound -> maximum speed
        )
    return ({"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target",
             "displayOrder": 1}, None, None)


def _exec_step(step: dict[str, Any], order: int) -> ExecutableStep:
    st_id, st_key, st_display = _STEP_TYPES.get(step.get("type", "run"), _STEP_TYPES["run"])
    target, v_min, v_max = _target(step)
    kwargs: dict[str, Any] = {
        "stepOrder": order,
        "stepType": {"stepTypeId": st_id, "stepTypeKey": st_key, "displayOrder": st_display},
        "endCondition": _end_condition(step.get("end_kind", "time")),
        "endConditionValue": float(step.get("end_value") or 0),
        "targetType": target,
    }
    if v_min and v_max:
        kwargs["targetValueOne"] = v_min
        kwargs["targetValueTwo"] = v_max
    if step.get("note"):
        kwargs["description"] = str(step["note"])[:512]
    return ExecutableStep(**kwargs)


def estimate_seconds(steps: list[dict[str, Any]]) -> int:
    total = 0.0
    for s in steps:
        if s.get("type") == "repeat":
            total += (s.get("iterations") or 1) * estimate_seconds(s.get("steps") or [])
            continue
        val = float(s.get("end_value") or 0)
        if s.get("end_kind") == "distance":
            pace = s.get("pace_high_sec") or s.get("pace_low_sec") or DEFAULT_PACE_SEC
            total += val / 1000.0 * pace
        else:
            total += val
    return int(round(total))


def build_running_workout(name: str, steps: list[dict[str, Any]]) -> RunningWorkout:
    """Our steps -> a Garmin RunningWorkout ready for upload."""
    counter = {"n": 0}

    def nxt() -> int:
        counter["n"] += 1
        return counter["n"]

    top: list[Any] = []
    for s in steps:
        if s.get("type") == "repeat":
            group_order = nxt()
            children = [_exec_step(cs, nxt()) for cs in (s.get("steps") or [])]
            top.append(create_repeat_group(int(s.get("iterations") or 1), children, group_order))
        else:
            top.append(_exec_step(s, nxt()))

    return RunningWorkout(
        workoutName=(name or "RunFlow workout")[:80],
        estimatedDurationInSecs=estimate_seconds(steps),
        workoutSegments=[WorkoutSegment(
            segmentOrder=1,
            sportType={"sportTypeId": 1, "sportTypeKey": "running"},
            workoutSteps=top,
        )],
    )
