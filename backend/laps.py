"""
Lap detection: find laps by detecting when the runner passes the start point again.

Uses GPS latlng stream to find re-entries into a start zone (~25m radius).
"""

import math
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def detect_laps(
    latlng_stream: list[list[float]],
    distance_stream: list[float],
    time_stream: list[int],
    zone_radius_m: float = 25,
    min_lap_distance_m: float = 150,
) -> dict[str, Any] | None:
    """
    Detect laps by finding re-entries into the start zone.

    Args:
        latlng_stream: [[lat, lng], ...] GPS points
        distance_stream: cumulative distance in meters
        time_stream: cumulative time in seconds
        zone_radius_m: radius of the "start zone" in meters
        min_lap_distance_m: minimum distance for a lap to count (filters GPS noise)

    Returns dict with laps list and stats, or None if < 2 laps.
    """
    if (not latlng_stream or not distance_stream or not time_stream
            or len(latlng_stream) < 20):
        return None

    start_lat = latlng_stream[0][0]
    start_lng = latlng_stream[0][1]

    # Find all points where runner enters the start zone
    # Track transitions: outside -> inside = one crossing
    in_zone = True  # starts in zone
    left_zone = False  # must leave zone before first lap counts
    crossings = [{"idx": 0, "dist": 0, "time": 0}]  # start point

    for i in range(1, len(latlng_stream)):
        dist_to_start = _haversine(
            latlng_stream[i][0], latlng_stream[i][1],
            start_lat, start_lng
        )

        if in_zone:
            if dist_to_start > zone_radius_m:
                in_zone = False
                left_zone = True
        else:
            if dist_to_start <= zone_radius_m and left_zone:
                # Re-entered start zone — check minimum distance
                dist_since_last = distance_stream[i] - crossings[-1]["dist"]
                if dist_since_last >= min_lap_distance_m:
                    crossings.append({
                        "idx": i,
                        "dist": distance_stream[i],
                        "time": time_stream[i],
                    })
                    in_zone = True

    if len(crossings) < 3:  # Need at least start + 2 crossings for 2 laps
        return None

    # Build laps from crossings
    laps = []
    for i in range(1, len(crossings)):
        lap_dist = crossings[i]["dist"] - crossings[i - 1]["dist"]
        lap_time = crossings[i]["time"] - crossings[i - 1]["time"]
        lap_pace = (lap_time / (lap_dist / 1000)) if lap_dist > 0 else 0

        laps.append({
            "lap_number": i,
            "distance_m": round(lap_dist),
            "duration_s": round(lap_time),
            "pace_sec_per_km": round(lap_pace, 1) if lap_pace > 0 else None,
            "start_idx": crossings[i - 1]["idx"],
            "end_idx": crossings[i]["idx"],
        })

    if len(laps) < 2:
        return None

    # Check if there's a partial lap at the end
    last_crossing = crossings[-1]
    remaining_dist = distance_stream[-1] - last_crossing["dist"]
    remaining_time = time_stream[-1] - last_crossing["time"]
    avg_lap_dist = sum(l["distance_m"] for l in laps) / len(laps)

    # Only show partial if it's > 30% of a full lap
    partial_lap = None
    if remaining_dist > avg_lap_dist * 0.3:
        partial_pace = (remaining_time / (remaining_dist / 1000)) if remaining_dist > 0 else 0
        partial_lap = {
            "distance_m": round(remaining_dist),
            "duration_s": round(remaining_time),
            "pace_sec_per_km": round(partial_pace, 1) if partial_pace > 0 else None,
        }

    # Stats
    times = [l["duration_s"] for l in laps]
    paces = [l["pace_sec_per_km"] for l in laps if l["pace_sec_per_km"]]
    fastest_idx = min(range(len(laps)), key=lambda i: laps[i]["duration_s"])
    slowest_idx = max(range(len(laps)), key=lambda i: laps[i]["duration_s"])

    return {
        "lap_count": len(laps),
        "avg_lap_distance_m": round(avg_lap_dist),
        "laps": laps,
        "partial_lap": partial_lap,
        "stats": {
            "fastest_lap": fastest_idx + 1,
            "fastest_time": laps[fastest_idx]["duration_s"],
            "fastest_pace": laps[fastest_idx]["pace_sec_per_km"],
            "slowest_lap": slowest_idx + 1,
            "slowest_time": laps[slowest_idx]["duration_s"],
            "avg_lap_time": round(sum(times) / len(times)),
            "avg_pace": round(sum(paces) / len(paces), 1) if paces else None,
        },
    }
