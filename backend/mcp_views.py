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


def cardiac_drift(hr: Optional[list[Optional[float]]]) -> Optional[float]:
    """Second-half mean HR minus first-half mean HR.

    Read this alongside the zone shares, never alone: a run that finally starts
    easy pushes the first-half mean down and so reports *higher* drift than a
    run that was hard from the gun. The metric punishes good pacing, so high
    drift with a falling Zone 5 share is improvement, not regression.
    """
    vals = [h for h in (hr or []) if h]
    if len(vals) < 2:
        return None
    mid = len(vals) // 2
    first, second = vals[:mid], vals[mid:]
    return round(sum(second) / len(second) - sum(first) / len(first), 1)


def seconds_to_cross(
    hr: Optional[list[Optional[float]]],
    time: Optional[list[Optional[int]]],
    bpm: int,
) -> Optional[int]:
    """When HR first reached `bpm` — how long the aerobic part of the run lasted."""
    if not hr or not time:
        return None
    for i, h in enumerate(hr):
        if h and h >= bpm and i < len(time) and time[i] is not None:
            return time[i]
    return None


def zone_shares(zones: Optional[list[dict[str, Any]]]) -> dict[int, float]:
    """Percentage of recorded time in each HR zone."""
    rows = zones or []
    total = sum((z.get("secs") or 0.0) for z in rows)
    if not total:
        return {}
    return {
        int(z["zone"]): round(100.0 * (z.get("secs") or 0.0) / total, 1)
        for z in rows
        if z.get("zone") is not None
    }


# Keys whose values are large and almost never what was being asked for.
HEAVY_KEYS = ("map_summary_polyline", "streams", "polyline")
MAX_STREAM_POINTS = 200
MAX_RESPONSE_CHARS = 25_000


def strip_heavy(value: Any) -> Any:
    """Recursively drop encoded polylines and raw streams from an API payload."""
    if isinstance(value, dict):
        return {k: strip_heavy(v) for k, v in value.items() if k not in HEAVY_KEYS}
    if isinstance(value, list):
        return [strip_heavy(v) for v in value]
    return value


def downsample(data: list[Any], max_points: int = MAX_STREAM_POINTS) -> dict[str, Any]:
    """Thin a stream to at most `max_points`, keeping the first and last sample.

    `original_samples` is returned so a thinned series is never mistaken for the
    whole thing.
    """
    n = len(data)
    if n <= max_points:
        return {"original_samples": n, "data": data}
    step = max(1, n // (max_points - 1))
    thinned = data[::step][: max_points - 1]
    if thinned and thinned[-1] != data[-1]:
        thinned.append(data[-1])
    return {"original_samples": n, "data": thinned}


def cap_text(text: str, limit: int = MAX_RESPONSE_CHARS) -> str:
    """Hard cap on tool output, truncating visibly.

    A silent truncation that reads as a complete answer is the failure being
    prevented here.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} characters of {len(text)}]"
