"""Compact, pre-digested views of a run, for the MCP tool surface.

Pure functions: streams and rows in, small dicts and strings out. No DB, no
network — so every number here is unit-testable in isolation, the same way
`fitness_model.py` is.

Why this module exists: the REST payloads carry encoded polylines and
~1,240-sample streams per run. Handing those to a model spends the context
window on data and buries the three numbers that answer the question.
"""
from __future__ import annotations

from typing import Any, Optional

# A gap longer than this with no distance gained is the watch auto-pausing,
# not sparse sampling.
PAUSE_GAP_SEC = 3
# Implied speed across a gap below which the runner was standing still. Using a
# speed rather than exact distance equality matters: auto-pause usually records
# an identical distance, but GPS drift can add a metre or two across an
# 11-minute stop, and that must still read as a stop rather than as movement.
PAUSE_MAX_SPEED_MPS = 0.5


def find_pauses(
    time: Optional[list[Optional[int]]],
    dist: Optional[list[Optional[float]]],
    hr: Optional[list[Optional[float]]] = None,
) -> list[dict[str, Any]]:
    """Where the run stopped: location, duration, and HR either side.

    HR entering and leaving a pause is the useful part — a drop from 191 to 148
    says the heart recovered while the legs did not, which is why the segment
    after a pause is often faster than anything before it.
    """
    if not time or not dist or len(time) != len(dist):
        return []
    out = []
    for i in range(len(time) - 1):
        t0, t1 = time[i], time[i + 1]
        d0, d1 = dist[i], dist[i + 1]
        if t0 is None or t1 is None or d0 is None or d1 is None:
            continue
        gap = t1 - t0
        if gap <= PAUSE_GAP_SEC or (d1 - d0) / gap > PAUSE_MAX_SPEED_MPS:
            continue
        out.append({
            "at_km": round(d0 / 1000.0, 3),
            "seconds": gap,
            "hr_in": hr[i] if hr and i < len(hr) else None,
            "hr_out": hr[i + 1] if hr and i + 1 < len(hr) else None,
        })
    return out


def moving_time_axis(
    time: Optional[list[Optional[int]]],
    dist: Optional[list[Optional[float]]],
) -> list[int]:
    """Rebuild the time axis with paused seconds removed."""
    if not time or not dist:
        return []
    axis, acc = [], 0
    for i in range(len(time)):
        if i:
            t0, t1 = time[i - 1], time[i]
            d0, d1 = dist[i - 1], dist[i]
            if t0 is None or t1 is None:
                acc += 1
            else:
                gap = t1 - t0
                moving = (
                    gap <= PAUSE_GAP_SEC
                    or ((d1 or 0.0) - (d0 or 0.0)) / gap > PAUSE_MAX_SPEED_MPS
                )
                acc += gap if moving else 1
        axis.append(acc)
    return axis


def split_table(
    dist: Optional[list[Optional[float]]],
    time: Optional[list[Optional[int]]],
    hr: Optional[list[Optional[float]]],
    metres: int = 500,
) -> list[dict[str, Any]]:
    """Per-`metres` splits on moving time, with mean and peak HR per split."""
    if not dist or not time:
        return []
    axis = moving_time_axis(time, dist)
    marks, nxt = [], metres
    for i, m in enumerate(dist):
        if m is not None and m >= nxt:
            marks.append((nxt, axis[i], i))
            nxt += metres

    rows, prev_t, prev_i = [], 0, 0
    for end_m, t, i in marks:
        seg = [h for h in (hr or [])[prev_i:i] if h]
        elapsed = t - prev_t
        rows.append({
            "from_m": end_m - metres,
            "to_m": end_m,
            "pace_sec_per_km": round(elapsed / (metres / 1000.0)),
            "hr_avg": round(sum(seg) / len(seg), 1) if seg else None,
            "hr_peak": max(seg) if seg else None,
        })
        prev_t, prev_i = t, i
    return rows
