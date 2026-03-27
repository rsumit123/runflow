"""
Interval analysis: two modes:
1. Auto: find N fastest non-overlapping segments of a given distance.
2. Timed: user provides warmup/rest durations, we slice the time stream.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def analyze_intervals(
    distance_stream: list[float],
    time_stream: list[int],
    rep_count: int,
    rep_distance_m: int,
) -> dict[str, Any] | None:
    """
    Find the N fastest non-overlapping segments of rep_distance_m meters.

    Returns structured breakdown: warmup, reps, rests, cooldown.
    """
    if not distance_stream or not time_stream or len(distance_stream) < 10:
        return None

    total_distance = distance_stream[-1]
    if total_distance < rep_distance_m:
        return None

    # Step 1: Find ALL possible windows of rep_distance_m
    # Each window: (time, start_idx, end_idx)
    windows = []
    i = 0
    for j in range(1, len(distance_stream)):
        seg_dist = distance_stream[j] - distance_stream[i]

        # Move start pointer forward while segment is longer than needed
        while i < j and (distance_stream[j] - distance_stream[i + 1]) >= rep_distance_m:
            i += 1
            seg_dist = distance_stream[j] - distance_stream[i]

        if seg_dist >= rep_distance_m:
            seg_time = time_stream[j] - time_stream[i]
            if seg_time > 0:
                windows.append({
                    "time": seg_time,
                    "start_idx": i,
                    "end_idx": j,
                    "start_dist": distance_stream[i],
                    "end_dist": distance_stream[j],
                    "start_time": time_stream[i],
                    "end_time": time_stream[j],
                })

    if not windows:
        return None

    # Step 2: Find top N non-overlapping windows (greedy: pick fastest, remove overlaps, repeat)
    windows.sort(key=lambda w: w["time"])
    selected = []

    for w in windows:
        # Check if this window overlaps with any already selected
        overlaps = False
        for s in selected:
            if not (w["end_idx"] <= s["start_idx"] or w["start_idx"] >= s["end_idx"]):
                overlaps = True
                break
        if not overlaps:
            selected.append(w)
            if len(selected) == rep_count:
                break

    if len(selected) < rep_count:
        # Couldn't find enough non-overlapping reps
        return {
            "is_interval": False,
            "message": f"Could only find {len(selected)} non-overlapping {rep_distance_m}m segments (requested {rep_count})",
        }

    # Step 3: Sort selected by position (start_dist)
    selected.sort(key=lambda w: w["start_dist"])

    # Step 4: Build segments: warmup → [rep → rest]* → rep → cooldown
    segments = []

    for rep_num, rep in enumerate(selected):
        # Before this rep: warmup (if first) or rest
        if rep_num == 0:
            # Warmup = start of run to start of first rep
            if rep["start_dist"] > 50:  # Only show warmup if > 50m
                warmup_time = rep["start_time"] - time_stream[0]
                warmup_dist = rep["start_dist"]
                warmup_pace = (warmup_time / (warmup_dist / 1000)) if warmup_dist > 0 else 0
                segments.append({
                    "type": "warmup",
                    "distance_m": round(warmup_dist),
                    "duration_s": round(warmup_time),
                    "pace_sec_per_km": round(warmup_pace, 1) if warmup_pace > 0 else None,
                })
        else:
            # Rest = end of previous rep to start of this rep
            prev = selected[rep_num - 1]
            rest_dist = rep["start_dist"] - prev["end_dist"]
            rest_time = rep["start_time"] - prev["end_time"]
            rest_pace = (rest_time / (rest_dist / 1000)) if rest_dist > 0 else 0
            if rest_dist > 10:  # Only show rest if > 10m
                segments.append({
                    "type": "rest",
                    "rest_number": rep_num,
                    "distance_m": round(rest_dist),
                    "duration_s": round(rest_time),
                    "pace_sec_per_km": round(rest_pace, 1) if rest_pace > 0 else None,
                })

        # The rep itself
        rep_dist = rep["end_dist"] - rep["start_dist"]
        rep_time = rep["end_time"] - rep["start_time"]
        rep_pace = (rep_time / (rep_dist / 1000)) if rep_dist > 0 else 0
        segments.append({
            "type": "rep",
            "rep_number": rep_num + 1,
            "distance_m": round(rep_dist),
            "duration_s": round(rep_time),
            "pace_sec_per_km": round(rep_pace, 1) if rep_pace > 0 else None,
        })

    # Cooldown = end of last rep to end of run
    last_rep = selected[-1]
    cooldown_dist = total_distance - last_rep["end_dist"]
    cooldown_time = time_stream[-1] - last_rep["end_time"]
    if cooldown_dist > 50:
        cooldown_pace = (cooldown_time / (cooldown_dist / 1000)) if cooldown_dist > 0 else 0
        segments.append({
            "type": "cooldown",
            "distance_m": round(cooldown_dist),
            "duration_s": round(cooldown_time),
            "pace_sec_per_km": round(cooldown_pace, 1) if cooldown_pace > 0 else None,
        })

    # Summary
    reps = [s for s in segments if s["type"] == "rep"]
    rests = [s for s in segments if s["type"] == "rest"]

    avg_rep_pace = sum(s["pace_sec_per_km"] for s in reps if s["pace_sec_per_km"]) / len(reps) if reps else None
    avg_rest_duration = sum(s["duration_s"] for s in rests) / len(rests) if rests else 0
    fastest_rep = min(reps, key=lambda s: s["pace_sec_per_km"] or float("inf")) if reps else None
    slowest_rep = max(reps, key=lambda s: s["pace_sec_per_km"] or 0) if reps else None

    return _build_summary(segments, rep_distance_m)


def _build_summary(segments, rep_distance_m):
    """Build the interval summary from segments."""
    reps = [s for s in segments if s["type"] == "rep"]
    rests = [s for s in segments if s["type"] == "rest"]

    avg_rep_pace = sum(s["pace_sec_per_km"] for s in reps if s["pace_sec_per_km"]) / len(reps) if reps else None
    avg_rest_duration = sum(s["duration_s"] for s in rests) / len(rests) if rests else 0
    fastest_rep = min(reps, key=lambda s: s["pace_sec_per_km"] or float("inf")) if reps else None
    slowest_rep = max(reps, key=lambda s: s["pace_sec_per_km"] or 0) if reps else None

    return {
        "is_interval": True,
        "segments": segments,
        "summary": {
            "total_reps": len(reps),
            "total_rests": len(rests),
            "rep_distance_m": rep_distance_m,
            "avg_rep_pace": round(avg_rep_pace, 1) if avg_rep_pace else None,
            "avg_rest_duration_s": round(avg_rest_duration),
            "fastest_rep": fastest_rep["rep_number"] if fastest_rep else None,
            "fastest_rep_pace": fastest_rep["pace_sec_per_km"] if fastest_rep else None,
            "slowest_rep": slowest_rep["rep_number"] if slowest_rep else None,
            "slowest_rep_pace": slowest_rep["pace_sec_per_km"] if slowest_rep else None,
        },
    }


def _find_idx_at_time(time_stream, target_time):
    """Find the stream index closest to a target time."""
    for i, t in enumerate(time_stream):
        if t >= target_time:
            return i
    return len(time_stream) - 1


def analyze_intervals_timed(
    distance_stream: list[float],
    time_stream: list[int],
    rep_count: int,
    rep_distance_m: int,
    warmup_s: int = 0,
    rest_s: int = 0,
) -> dict[str, Any] | None:
    """
    Slice intervals using known timing: warmup duration + rest duration.

    After warmup_s seconds, each rep is the next rep_distance_m meters,
    followed by rest_s seconds, repeated rep_count times.
    """
    if not distance_stream or not time_stream or len(distance_stream) < 10:
        return None

    total_distance = distance_stream[-1]
    total_time = time_stream[-1]
    segments = []

    current_time = 0

    # Warmup
    if warmup_s > 0:
        warmup_end_idx = _find_idx_at_time(time_stream, warmup_s)
        warmup_dist = distance_stream[warmup_end_idx]
        warmup_pace = (warmup_s / (warmup_dist / 1000)) if warmup_dist > 0 else 0
        segments.append({
            "type": "warmup",
            "distance_m": round(warmup_dist),
            "duration_s": warmup_s,
            "pace_sec_per_km": round(warmup_pace, 1) if warmup_pace > 0 else None,
        })
        current_time = warmup_s

    # Reps and rests
    for rep_num in range(1, rep_count + 1):
        # Find rep start index (at current_time)
        rep_start_idx = _find_idx_at_time(time_stream, current_time)
        rep_start_dist = distance_stream[rep_start_idx]

        # Find rep end: where distance reaches rep_start_dist + rep_distance_m
        rep_end_dist = rep_start_dist + rep_distance_m
        rep_end_idx = rep_start_idx
        for i in range(rep_start_idx, len(distance_stream)):
            if distance_stream[i] >= rep_end_dist:
                rep_end_idx = i
                break
        else:
            rep_end_idx = len(distance_stream) - 1

        actual_dist = distance_stream[rep_end_idx] - distance_stream[rep_start_idx]
        rep_time = time_stream[rep_end_idx] - time_stream[rep_start_idx]
        rep_pace = (rep_time / (actual_dist / 1000)) if actual_dist > 0 else 0

        segments.append({
            "type": "rep",
            "rep_number": rep_num,
            "distance_m": round(actual_dist),
            "duration_s": round(rep_time),
            "pace_sec_per_km": round(rep_pace, 1) if rep_pace > 0 else None,
        })

        current_time = time_stream[rep_end_idx]

        # Rest (except after last rep)
        if rep_num < rep_count and rest_s > 0:
            rest_end_time = current_time + rest_s
            rest_end_idx = _find_idx_at_time(time_stream, rest_end_time)
            rest_dist = distance_stream[rest_end_idx] - distance_stream[rep_end_idx]
            rest_pace = (rest_s / (rest_dist / 1000)) if rest_dist > 0 else 0

            segments.append({
                "type": "rest",
                "rest_number": rep_num,
                "distance_m": round(rest_dist),
                "duration_s": rest_s,
                "pace_sec_per_km": round(rest_pace, 1) if rest_pace > 0 else None,
            })
            current_time = rest_end_time

    # Cooldown
    cooldown_time = total_time - current_time
    if cooldown_time > 10:
        last_idx = _find_idx_at_time(time_stream, current_time)
        cooldown_dist = total_distance - distance_stream[last_idx]
        cooldown_pace = (cooldown_time / (cooldown_dist / 1000)) if cooldown_dist > 0 else 0
        segments.append({
            "type": "cooldown",
            "distance_m": round(cooldown_dist),
            "duration_s": round(cooldown_time),
            "pace_sec_per_km": round(cooldown_pace, 1) if cooldown_pace > 0 else None,
        })

    return _build_summary(segments, rep_distance_m)
