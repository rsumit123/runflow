"""
Route matching: detect when activities follow the same route.

Groups activities by comparing GPS data. Two runs match if:
  - Start points are within 300m of each other
  - Distance is within 15% of each other
  - AND one of:
    a) End points are within 300m (point-to-point match)
    b) Both are loops (start ≈ end within 500m) with start points close
    c) Polyline overlap > 60% (shape similarity)
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


def _points_close(p1: list | None, p2: list | None, threshold_m: float = 300) -> bool:
    """Check if two [lat, lng] points are within threshold meters."""
    if not p1 or not p2 or len(p1) < 2 or len(p2) < 2:
        return False
    return _haversine(p1[0], p1[1], p2[0], p2[1]) <= threshold_m


def _is_loop(start: list | None, end: list | None, threshold_m: float = 500) -> bool:
    """Check if a run is a loop (start ≈ end)."""
    return _points_close(start, end, threshold_m)


def _distance_similar(d1: float | None, d2: float | None, tolerance: float = 0.15) -> bool:
    """Check if two distances are within tolerance (15%) of each other."""
    if not d1 or not d2 or d1 <= 0 or d2 <= 0:
        return False
    ratio = min(d1, d2) / max(d1, d2)
    return ratio >= (1 - tolerance)


def _decode_polyline(encoded: str) -> list[list[float]]:
    """Decode a Google-encoded polyline into [[lat, lng], ...]."""
    if not encoded:
        return []
    points = []
    index = 0
    lat = 0
    lng = 0
    while index < encoded.length if hasattr(encoded, 'length') else index < len(encoded):
        for val_ref in [None, None]:
            shift = 0
            result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if val_ref is None:
                lat += delta
            else:
                lng += delta
            if val_ref is None:
                val_ref = True  # first pass done
                # Actually need to handle this differently
                break
        # Simpler approach:
        pass
    # Use a simpler decoder
    return _simple_decode(encoded)


def _simple_decode(encoded: str) -> list[list[float]]:
    """Simple polyline decoder."""
    points = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for _ in range(2):
            shift = 0
            result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if _ == 0:
                lat += delta
            else:
                lng += delta
        points.append([lat / 1e5, lng / 1e5])
    return points


def _polyline_overlap(poly1: str | None, poly2: str | None, threshold_m: float = 100) -> float:
    """
    Calculate overlap percentage between two polylines.
    Samples points from poly1 and checks how many are within threshold_m of any point in poly2.
    Returns overlap ratio 0.0-1.0.
    """
    if not poly1 or not poly2:
        return 0.0

    pts1 = _simple_decode(poly1)
    pts2 = _simple_decode(poly2)

    if len(pts1) < 3 or len(pts2) < 3:
        return 0.0

    # Sample every few points to keep it fast
    sample1 = pts1[::max(1, len(pts1) // 20)]
    sample2 = pts2[::max(1, len(pts2) // 20)]

    if not sample1 or not sample2:
        return 0.0

    matches = 0
    for p1 in sample1:
        for p2 in sample2:
            if _haversine(p1[0], p1[1], p2[0], p2[1]) <= threshold_m:
                matches += 1
                break

    return matches / len(sample1)


def _routes_match(act: dict, ref: dict) -> bool:
    """Check if two activities are on the same route."""
    # Must have similar distance
    if not _distance_similar(act["distance"], ref["distance"]):
        return False

    # Must start close to each other
    if not _points_close(act["start_latlng"], ref["start_latlng"]):
        return False

    # Check end points match (covers point-to-point routes)
    if _points_close(act.get("end_latlng"), ref.get("end_latlng")):
        return True

    # Both are loops → start point + distance match is enough
    if (_is_loop(act["start_latlng"], act.get("end_latlng"))
            and _is_loop(ref["start_latlng"], ref.get("end_latlng"))):
        return True

    # Fall back to polyline shape comparison if available
    if act.get("polyline") and ref.get("polyline"):
        overlap = _polyline_overlap(act["polyline"], ref["polyline"])
        if overlap >= 0.6:
            return True

    return False


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
    # Filter to activities with GPS data (end_latlng optional for loops)
    gps_activities = [
        a for a in activities
        if a.get("start_latlng") and a.get("distance")
    ]

    if not gps_activities:
        return []

    # Group by matching route
    routes: list[list[dict]] = []

    for act in gps_activities:
        matched = False
        for route in routes:
            ref = route[0]
            if _routes_match(act, ref):
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

        # Generate stable route key from start coords + distance bucket
        ref_start = route[0]["start_latlng"]
        avg_dist = sum(a["distance"] for a in route if a.get("distance")) / len(route)
        route_key = f"{round(ref_start[0], 3)}_{round(ref_start[1], 3)}_{round(avg_dist / 100) * 100}"

        # Most common name
        names = {}
        for a in route:
            n = a.get("name") or "Untitled"
            names[n] = names.get(n, 0) + 1
        route_name = max(names, key=names.get)

        # Stats
        times = [a["moving_time"] for a in route if a.get("moving_time")]
        # Exclude interval runs from pace calculations
        non_interval = [a for a in route if not a.get("is_interval")]
        speeds = [a["average_speed"] for a in non_interval if a.get("average_speed") and a["average_speed"] > 0]
        distances = [a["distance"] for a in route if a.get("distance")]

        best_time = min(times) if times else None
        best_time_id = None
        if best_time:
            best_time_id = next((a["id"] for a in route if a.get("moving_time") == best_time), None)

        best_pace_sec = None
        best_pace_id = None
        if speeds:
            fastest = max(non_interval, key=lambda a: a.get("average_speed") or 0)
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
                "is_interval": a.get("is_interval", False),
            })

        result.append({
            "route_id": route_id,
            "route_key": route_key,
            "name": route_name,
            "run_count": len(route),
            "avg_distance_km": round(avg_distance / 1000, 2),
            "best_time": best_time,
            "best_time_id": best_time_id,
            "best_pace_sec_per_km": round(best_pace_sec, 1) if best_pace_sec else None,
            "best_pace_id": best_pace_id,
            "start_latlng": route[0]["start_latlng"],
            "activities": run_summaries,
            "activity_ids": [a["id"] for a in route],
        })

    # Sort by run count descending
    result.sort(key=lambda r: r["run_count"], reverse=True)

    return result
