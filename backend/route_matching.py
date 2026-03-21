"""
Route matching: detect when activities follow the same route.

Groups activities by comparing start/end GPS coordinates and total distance.
Two runs match if:
  - Start points are within 200m of each other
  - End points are within 200m of each other
  - Total distance is within 15% of each other
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


def _points_close(p1: list | None, p2: list | None, threshold_m: float = 200) -> bool:
    """Check if two [lat, lng] points are within threshold meters."""
    if not p1 or not p2 or len(p1) < 2 or len(p2) < 2:
        return False
    return _haversine(p1[0], p1[1], p2[0], p2[1]) <= threshold_m


def _distance_similar(d1: float | None, d2: float | None, tolerance: float = 0.15) -> bool:
    """Check if two distances are within tolerance (15%) of each other."""
    if not d1 or not d2 or d1 <= 0 or d2 <= 0:
        return False
    ratio = min(d1, d2) / max(d1, d2)
    return ratio >= (1 - tolerance)


def group_routes(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group activities into routes based on GPS similarity.

    Each activity dict should have: id, name, start_latlng, end_latlng, distance,
    moving_time, average_speed, start_date, total_elevation_gain.

    Returns a list of route groups, each with:
    - route_id: int
    - name: auto-generated from most common activity name
    - run_count: int
    - activities: list of activity summaries sorted by date
    - best_time: fastest moving_time
    - best_pace: fastest avg pace
    - avg_distance: average distance
    """
    # Filter to activities with GPS data
    gps_activities = [
        a for a in activities
        if a.get("start_latlng") and a.get("end_latlng") and a.get("distance")
    ]

    if not gps_activities:
        return []

    # Group by matching start/end/distance
    routes: list[list[dict]] = []

    for act in gps_activities:
        matched = False
        for route in routes:
            # Compare against the first activity in the route group
            ref = route[0]
            if (
                _points_close(act["start_latlng"], ref["start_latlng"])
                and _points_close(act["end_latlng"], ref["end_latlng"])
                and _distance_similar(act["distance"], ref["distance"])
            ):
                route.append(act)
                matched = True
                break

        if not matched:
            routes.append([act])

    # Build route summaries (only routes with 2+ runs are interesting)
    result = []
    route_id = 0
    for route in routes:
        if len(route) < 2:
            continue

        route_id += 1

        # Sort by date
        sorted_runs = sorted(route, key=lambda a: a.get("start_date") or "")

        # Most common name
        names = {}
        for a in route:
            n = a.get("name") or "Untitled"
            names[n] = names.get(n, 0) + 1
        route_name = max(names, key=names.get)

        # Stats
        times = [a["moving_time"] for a in route if a.get("moving_time")]
        speeds = [a["average_speed"] for a in route if a.get("average_speed") and a["average_speed"] > 0]
        distances = [a["distance"] for a in route if a.get("distance")]

        best_time = min(times) if times else None
        best_time_id = None
        if best_time:
            best_time_id = next((a["id"] for a in route if a.get("moving_time") == best_time), None)

        best_pace_sec = None
        best_pace_id = None
        if speeds:
            fastest = max(route, key=lambda a: a.get("average_speed") or 0)
            best_pace_sec = 1000 / fastest["average_speed"] if fastest["average_speed"] else None
            best_pace_id = fastest["id"]

        avg_distance = sum(distances) / len(distances) if distances else 0

        # Per-run summaries
        run_summaries = []
        for a in sorted_runs:
            pace = None
            if a.get("average_speed") and a["average_speed"] > 0:
                pace = 1000 / a["average_speed"]
            run_summaries.append({
                "id": a["id"],
                "name": a.get("name"),
                "date": a.get("start_date"),
                "distance": a.get("distance"),
                "moving_time": a.get("moving_time"),
                "pace_sec_per_km": round(pace, 1) if pace else None,
                "elevation_gain": a.get("total_elevation_gain"),
            })

        result.append({
            "route_id": route_id,
            "name": route_name,
            "run_count": len(route),
            "avg_distance_km": round(avg_distance / 1000, 2),
            "best_time": best_time,
            "best_time_id": best_time_id,
            "best_pace_sec_per_km": round(best_pace_sec, 1) if best_pace_sec else None,
            "best_pace_id": best_pace_id,
            "start_latlng": route[0]["start_latlng"],
            "activities": run_summaries,
        })

    # Sort by run count descending
    result.sort(key=lambda r: r["run_count"], reverse=True)

    return result
