"""
Interval detection: analyze distance/time streams to find workout segments.

Detects warmup, workout reps, rest periods, and cooldown from pace transitions.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Minimum segment length to count as a rep or rest
MIN_SEGMENT_METERS = 30
MIN_SEGMENT_SECONDS = 10


def detect_intervals(
    distance_stream: list[float],
    time_stream: list[int],
    window_meters: float = 50,
) -> dict[str, Any] | None:
    """
    Detect interval segments from distance/time streams.

    Returns a dict with:
    - segments: list of { type, start_dist, end_dist, start_time, end_time, distance, duration, pace_sec_per_km }
    - summary: { warmup, reps, rests, cooldown counts and totals }
    """
    if not distance_stream or not time_stream or len(distance_stream) < 20:
        return None

    total_distance = distance_stream[-1]
    if total_distance < 200:
        return None

    # Step 1: Compute rolling pace every ~window_meters
    pace_points = []
    i = 0
    for j in range(1, len(distance_stream)):
        seg_dist = distance_stream[j] - distance_stream[i]
        if seg_dist >= window_meters:
            seg_time = time_stream[j] - time_stream[i]
            if seg_time > 0:
                pace = seg_time / (seg_dist / 1000)  # sec per km
                mid_dist = (distance_stream[i] + distance_stream[j]) / 2
                mid_time = (time_stream[i] + time_stream[j]) / 2
                pace_points.append({
                    "pace": pace,
                    "dist": mid_dist,
                    "time": mid_time,
                    "start_idx": i,
                    "end_idx": j,
                })
            i = j

    if len(pace_points) < 4:
        return None

    # Step 2: Find fast and slow pace thresholds using clustering
    # Sort paces to find natural break point
    sorted_paces = sorted(p["pace"] for p in pace_points)

    # Use the gap between fastest 40% and slowest 40% as threshold
    n = len(sorted_paces)
    fast_paces = sorted_paces[:int(n * 0.4)]
    slow_paces = sorted_paces[int(n * 0.6):]

    if not fast_paces or not slow_paces:
        return None

    fast_avg = sum(fast_paces) / len(fast_paces)
    slow_avg = sum(slow_paces) / len(slow_paces)

    # Need at least 20% pace difference between fast and slow
    if slow_avg < fast_avg * 1.2:
        return None  # Not an interval run — pace is too uniform

    # Threshold = midpoint between fast and slow averages
    threshold = (fast_avg + slow_avg) / 2

    # Step 3: Label each pace point as fast or slow
    for p in pace_points:
        p["label"] = "fast" if p["pace"] < threshold else "slow"

    # Step 4: Group consecutive same-label points into segments
    raw_segments = []
    current_label = pace_points[0]["label"]
    seg_start = 0

    for i in range(1, len(pace_points)):
        if pace_points[i]["label"] != current_label:
            raw_segments.append({
                "label": current_label,
                "points": pace_points[seg_start:i],
            })
            current_label = pace_points[i]["label"]
            seg_start = i

    raw_segments.append({
        "label": current_label,
        "points": pace_points[seg_start:],
    })

    # Step 5: Filter out tiny segments (merge into neighbors)
    segments = []
    for seg in raw_segments:
        pts = seg["points"]
        start_dist = pts[0]["dist"] - window_meters / 2
        end_dist = pts[-1]["dist"] + window_meters / 2
        seg_distance = end_dist - start_dist
        start_time = pts[0]["time"] - (pts[0]["pace"] * window_meters / 1000 / 2)
        end_time = pts[-1]["time"] + (pts[-1]["pace"] * window_meters / 1000 / 2)
        duration = end_time - start_time

        if seg_distance < MIN_SEGMENT_METERS or duration < MIN_SEGMENT_SECONDS:
            # Too short — merge with previous if possible
            if segments:
                segments[-1]["points"].extend(pts)
            continue

        avg_pace = sum(p["pace"] for p in pts) / len(pts)
        segments.append({
            "label": seg["label"],
            "points": pts,
            "start_dist": max(0, round(start_dist)),
            "end_dist": round(min(end_dist, total_distance)),
            "start_time": max(0, round(start_time)),
            "end_time": round(end_time),
            "distance": round(seg_distance),
            "duration": round(duration),
            "pace_sec_per_km": round(avg_pace, 1),
        })

    if len(segments) < 3:
        return None  # Need at least warmup + rep + cooldown

    # Step 6: Label as warmup/rep/rest/cooldown
    result_segments = []
    rep_count = 0
    rest_count = 0
    found_first_fast = False
    last_fast_idx = -1

    # Find last fast segment index
    for i in range(len(segments) - 1, -1, -1):
        if segments[i]["label"] == "fast":
            last_fast_idx = i
            break

    for i, seg in enumerate(segments):
        if not found_first_fast:
            if seg["label"] == "fast":
                found_first_fast = True
                rep_count += 1
                seg["type"] = "rep"
                seg["rep_number"] = rep_count
            else:
                seg["type"] = "warmup"
        elif i > last_fast_idx:
            seg["type"] = "cooldown"
        elif seg["label"] == "fast":
            rep_count += 1
            seg["type"] = "rep"
            seg["rep_number"] = rep_count
        else:
            rest_count += 1
            seg["type"] = "rest"
            seg["rest_number"] = rest_count

        result_segments.append({
            "type": seg["type"],
            "rep_number": seg.get("rep_number"),
            "rest_number": seg.get("rest_number"),
            "start_dist_m": seg["start_dist"],
            "end_dist_m": seg["end_dist"],
            "distance_m": seg["distance"],
            "start_time_s": seg["start_time"],
            "end_time_s": seg["end_time"],
            "duration_s": seg["duration"],
            "pace_sec_per_km": seg["pace_sec_per_km"],
        })

    # Summary
    reps = [s for s in result_segments if s["type"] == "rep"]
    rests = [s for s in result_segments if s["type"] == "rest"]
    warmups = [s for s in result_segments if s["type"] == "warmup"]
    cooldowns = [s for s in result_segments if s["type"] == "cooldown"]

    avg_rep_pace = sum(s["pace_sec_per_km"] for s in reps) / len(reps) if reps else None
    avg_rest_pace = sum(s["pace_sec_per_km"] for s in rests) / len(rests) if rests else None

    return {
        "is_interval": True,
        "segments": result_segments,
        "summary": {
            "total_reps": len(reps),
            "total_rests": len(rests),
            "avg_rep_distance_m": round(sum(s["distance_m"] for s in reps) / len(reps)) if reps else 0,
            "avg_rep_pace": round(avg_rep_pace, 1) if avg_rep_pace else None,
            "avg_rest_pace": round(avg_rest_pace, 1) if avg_rest_pace else None,
            "avg_rep_duration_s": round(sum(s["duration_s"] for s in reps) / len(reps)) if reps else 0,
            "avg_rest_duration_s": round(sum(s["duration_s"] for s in rests) / len(rests)) if rests else 0,
            "warmup_distance_m": sum(s["distance_m"] for s in warmups),
            "cooldown_distance_m": sum(s["distance_m"] for s in cooldowns),
            "fast_threshold": round(threshold, 1),
        },
    }
