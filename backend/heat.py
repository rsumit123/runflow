"""
Heat-adjust pace targets, so an "easy" run stays easy when the air is against you.

Humidity, not temperature, is what actually breaks a runner: sweat can only cool
you if it evaporates. So the accepted approach pairs air temperature with DEW
POINT (how saturated the air is) rather than relative humidity alone — 30 °C at a
12 °C dew point is a pleasant evening; 30 °C at a 26 °C dew point is Jamshedpur in
July, and it will cost you the better part of a minute per km.

The band below is the long-standing temp+dew-point stress table used in distance
coaching, expressed as a percentage the pace should slow by.

We only ever widen EASY paces. Race and goal-pace targets belong to the runner.
"""
from __future__ import annotations

from typing import Any, Optional

# (max sum of °F temp + °F dew point, slowdown fraction, label)
STRESS_BANDS: list[tuple[int, float, str]] = [
    (100, 0.000, "ideal"),
    (110, 0.005, "mild"),
    (120, 0.010, "mild"),
    (130, 0.020, "moderate"),
    (140, 0.030, "moderate"),
    (150, 0.045, "hard"),
    (160, 0.060, "hard"),
    (170, 0.080, "severe"),
    (999, 0.100, "extreme"),
]

# Below this there is nothing worth telling the runner about.
MIN_MEANINGFUL_SEC = 5


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def stress_index(temp_c: float, dew_point_c: float) -> float:
    """Temp + dew point, in °F — the number the coaching table is indexed on."""
    return _c_to_f(temp_c) + _c_to_f(dew_point_c)


def _band(index: float) -> tuple[float, str]:
    for ceiling, frac, label in STRESS_BANDS:
        if index <= ceiling:
            return frac, label
    return STRESS_BANDS[-1][1], STRESS_BANDS[-1][2]


def _fmt(sec: Optional[float]) -> Optional[str]:
    if sec is None:
        return None
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def adjust(temp_c: float, dew_point_c: float, pace_low_sec: Optional[int],
           pace_high_sec: Optional[int]) -> dict[str, Any]:
    """Widen an easy pace band for the heat, and say by how much and why."""
    index = stress_index(temp_c, dew_point_c)
    frac, label = _band(index)

    base = pace_low_sec or pace_high_sec
    penalty = round(base * frac) if base else 0
    meaningful = penalty >= MIN_MEANINGFUL_SEC

    out: dict[str, Any] = {
        "temp_c": round(temp_c, 1),
        "dew_point_c": round(dew_point_c, 1),
        "stress_index": round(index),
        "level": label,
        "slowdown_pct": round(frac * 100, 1),
        "penalty_sec": penalty if meaningful else 0,
        "adjusted": bool(meaningful and base),
        "pace_low_sec": pace_low_sec,
        "pace_high_sec": pace_high_sec,
    }

    if meaningful and pace_low_sec and pace_high_sec:
        out["pace_low_sec"] = pace_low_sec + penalty
        out["pace_high_sec"] = pace_high_sec + penalty
        out["original_band"] = f"{_fmt(pace_low_sec)}-{_fmt(pace_high_sec)}"
        out["adjusted_band"] = f"{_fmt(pace_low_sec + penalty)}-{_fmt(pace_high_sec + penalty)}"
        out["detail"] = (
            f"{round(temp_c)}°C with a {round(dew_point_c)}°C dew point is {label} heat stress. "
            f"Sweat evaporates poorly in air this saturated, so the same effort costs you about "
            f"{penalty} s/km. Run {out['adjusted_band']}/km today — that is the SAME effort, "
            f"not an easier one. Chasing your usual pace in this would turn an easy run into a "
            f"hard one without you noticing."
        )
    elif label == "ideal":
        out["detail"] = (
            f"{round(temp_c)}°C with a {round(dew_point_c)}°C dew point — no heat penalty. "
            f"Run your normal targets."
        )
    else:
        out["detail"] = (
            f"{round(temp_c)}°C with a {round(dew_point_c)}°C dew point is {label} heat stress, "
            f"but the pace cost is under {MIN_MEANINGFUL_SEC} s/km — not worth changing your targets."
        )
    return out
