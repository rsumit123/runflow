"""
100m sprint target projection.

Sibling to the 5K projection module. Given the runner's *current* best 100m
time, projects realistic sub-second targets at fixed week horizons using a
capped per-week improvement rate, and sanity-checks a user-chosen target
against a trainable "floor". Pure functions — unit-testable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

PER_WEEK = 0.009    # ~0.9%/week 100m-time improvement (trained, short-sprint)
CAP = 0.12          # never project more than 12% total improvement


def sprint_projections(
    current_100m_sec: float,
    now: datetime,
    horizons: tuple[int, ...] = (8, 10, 12),
) -> dict[str, Any]:
    """Project 100m targets at each horizon.

    `now` is accepted for signature symmetry with the 5K module and is unused.
    """
    out_horizons = []
    for weeks in horizons:
        imp = min(CAP, PER_WEEK * weeks)
        out_horizons.append({
            "weeks": weeks,
            "target_100m_sec": round(current_100m_sec * (1 - imp), 1),
            "improvement_pct": round(imp * 100, 1),
        })
    return {
        "current_100m_sec": current_100m_sec,
        "horizons": out_horizons,
    }


def sprint_reality_check(
    current_100m_sec: float,
    target_100m_sec: float,
    weeks: int,
) -> dict[str, Any]:
    """Is a user-chosen 100m target trainable in `weeks`?

    The `floor` is the fastest time we'd realistically project from the current
    time over `weeks`. A target slower-or-equal to that floor is realistic.
    """
    imp = min(CAP, PER_WEEK * weeks)
    floor = round(current_100m_sec * (1 - imp), 1)
    realistic = target_100m_sec >= floor - 1e-9
    if realistic:
        note = (
            f"A target of {target_100m_sec:.1f}s in {weeks} weeks is realistic "
            f"from {current_100m_sec:.1f}s — train for it."
        )
    else:
        note = (
            f"A target of {target_100m_sec:.1f}s is faster than a realistic "
            f"ceiling of ~{floor:.1f}s over {weeks} weeks from "
            f"{current_100m_sec:.1f}s. Aim for around {floor:.1f}s instead."
        )
    return {
        "realistic": realistic,
        "floor_100m_sec": floor,
        "note": note,
    }
