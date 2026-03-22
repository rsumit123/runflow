"""
Run insights: generate narrative analysis comparing this run to history on the same route.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, Stream
from laps import detect_laps
from route_matching import _points_close, _distance_similar

logger = logging.getLogger(__name__)


def _format_pace(sec_per_km: float) -> str:
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}"


def _compute_pace_segments(distance_stream, time_stream, num_segments=4):
    """Split run into N equal segments and compute pace for each."""
    if not distance_stream or len(distance_stream) < 10:
        return []
    total = distance_stream[-1]
    seg_dist = total / num_segments
    segments = []
    prev_idx = 0

    for seg_num in range(1, num_segments + 1):
        target = seg_dist * seg_num
        # Find index closest to target distance
        idx = prev_idx
        for i in range(prev_idx, len(distance_stream)):
            if distance_stream[i] >= target:
                idx = i
                break
        else:
            idx = len(distance_stream) - 1

        d = distance_stream[idx] - distance_stream[prev_idx]
        t = time_stream[idx] - time_stream[prev_idx]
        pace = (t / (d / 1000)) if d > 0 else 0
        segments.append({
            "segment": seg_num,
            "distance": round(d),
            "time": round(t),
            "pace": round(pace, 1),
        })
        prev_idx = idx

    return segments


def _detect_pacing_pattern(segments):
    """Detect pacing pattern from segment paces."""
    if len(segments) < 3:
        return "unknown", ""

    paces = [s["pace"] for s in segments]
    first_half = sum(paces[:len(paces)//2]) / (len(paces)//2)
    second_half = sum(paces[len(paces)//2:]) / (len(paces) - len(paces)//2)

    diff = second_half - first_half

    if diff < -10:
        return "negative_split", "You ran a negative split — started slower and finished strong. This is excellent pacing strategy."
    elif diff > 15:
        return "fade", "You faded toward the end — your pace dropped significantly in the second half."
    elif diff > 5:
        return "slight_fade", "You slowed slightly in the second half — a small positive split."
    elif abs(diff) <= 5:
        return "even", "You maintained even pacing throughout — great consistency."
    else:
        return "negative_split", "You picked up pace in the second half — strong finish."


async def generate_run_insight(
    session: AsyncSession,
    activity_id: int,
) -> dict[str, Any] | None:
    """
    Generate insights for a run by comparing to history on the same route.
    """
    # Get this activity
    act = await session.get(Activity, activity_id)
    if not act or not act.distance or act.distance < 200:
        return None

    # Get streams for this activity
    stream_result = await session.execute(
        select(Stream).where(
            Stream.activity_id == activity_id,
            Stream.stream_type.in_(["distance", "time", "latlng"]),
        )
    )
    streams = {s.stream_type: s.data for s in stream_result.scalars().all()}
    dist_stream = streams.get("distance")
    time_stream = streams.get("time")
    latlng_stream = streams.get("latlng")

    if not dist_stream or not time_stream:
        return None

    # This run's stats
    this_pace = act.moving_time / (act.distance / 1000) if act.distance > 0 else 0

    # Compute pace segments for this run
    segments = _compute_pace_segments(dist_stream, time_stream)
    pattern, pattern_desc = _detect_pacing_pattern(segments)

    # Detect laps for this run
    this_laps = None
    if latlng_stream:
        this_laps = detect_laps(latlng_stream, dist_stream, time_stream)

    # Find similar runs on the same route (exclude very short runs = likely intervals)
    history_result = await session.execute(
        select(Activity)
        .where(
            Activity.id != activity_id,
            Activity.distance > act.distance * 0.5,
            Activity.start_latlng.isnot(None),
        )
        .order_by(Activity.start_date.desc())
    )
    all_activities = history_result.scalars().all()

    # Find route matches
    route_history = []
    for other in all_activities:
        if not other.start_latlng or not other.distance or not other.moving_time:
            continue
        if other.distance < 500:  # skip very short (interval-like)
            continue
        if (_points_close(act.start_latlng, other.start_latlng, 300)
                and _distance_similar(act.distance, other.distance, 0.25)):
            other_pace = other.moving_time / (other.distance / 1000)
            route_history.append({
                "id": other.id,
                "pace": other_pace,
                "distance": other.distance,
                "date": other.start_date,
                "moving_time": other.moving_time,
            })
        if len(route_history) >= 10:
            break

    # Build narrative
    narratives = []
    tips = []

    if route_history:
        avg_route_pace = sum(r["pace"] for r in route_history) / len(route_history)
        best_route_pace = min(r["pace"] for r in route_history)
        pace_diff = this_pace - avg_route_pace

        if pace_diff < -5:
            narratives.append(
                f"Faster than usual! Your pace of {_format_pace(this_pace)}/km was "
                f"{abs(round(pace_diff))}s faster than your average of {_format_pace(avg_route_pace)}/km "
                f"on this route ({len(route_history)} previous runs)."
            )
        elif pace_diff > 10:
            narratives.append(
                f"Slower than your usual pace on this route. You ran {_format_pace(this_pace)}/km "
                f"vs your average of {_format_pace(avg_route_pace)}/km "
                f"({round(pace_diff)}s slower)."
            )
            tips.append("Consider if you're fatigued — an easy day is fine for recovery.")
        else:
            narratives.append(
                f"Right on pace. Your {_format_pace(this_pace)}/km is close to your "
                f"route average of {_format_pace(avg_route_pace)}/km."
            )

        # Compare to best
        best_diff = this_pace - best_route_pace
        if best_diff <= 0:
            narratives.append(f"New route PR! You beat your previous best of {_format_pace(best_route_pace)}/km.")
        elif best_diff < 10:
            narratives.append(f"Just {round(best_diff)}s off your best pace on this route ({_format_pace(best_route_pace)}/km).")
    else:
        # No route history — compare to phase
        phase_result = await session.execute(
            select(Activity)
            .where(Activity.id != activity_id, Activity.distance > 500)
            .order_by(Activity.start_date.desc())
            .limit(10)
        )
        recent = phase_result.scalars().all()
        if recent:
            recent_paces = [r.moving_time / (r.distance / 1000) for r in recent if r.distance and r.moving_time and r.distance > 0]
            if recent_paces:
                avg_recent = sum(recent_paces) / len(recent_paces)
                diff = this_pace - avg_recent
                if abs(diff) > 5:
                    direction = "faster" if diff < 0 else "slower"
                    narratives.append(
                        f"Your pace of {_format_pace(this_pace)}/km was {abs(round(diff))}s {direction} "
                        f"than your recent average of {_format_pace(avg_recent)}/km."
                    )
                else:
                    narratives.append(f"Consistent pace of {_format_pace(this_pace)}/km, in line with your recent runs.")

    # Pacing pattern insight
    if pattern_desc:
        narratives.append(pattern_desc)

    # Pacing tips based on pattern
    if pattern == "fade" and segments:
        drop = segments[-1]["pace"] - segments[0]["pace"]
        tips.append(
            f"Your pace dropped {round(drop)}s/km from start to finish. "
            f"Try starting at {_format_pace(segments[0]['pace'] + 10)}/km instead of {_format_pace(segments[0]['pace'])}/km."
        )
    elif pattern == "slight_fade" and segments:
        tips.append("Try starting a few seconds slower per km to maintain pace through the end.")
    elif pattern == "negative_split":
        tips.append("Great pacing discipline! Keep this up — it's the optimal strategy for improvement.")

    # Lap consistency insight
    if this_laps and this_laps.get("lap_count", 0) >= 3:
        lap_times = [l["duration_s"] for l in this_laps["laps"]]
        spread = max(lap_times) - min(lap_times)
        avg_lap = sum(lap_times) / len(lap_times)

        if spread <= 5:
            narratives.append(f"Incredibly consistent laps — only {spread}s spread across {len(lap_times)} laps.")
        elif spread <= 15:
            narratives.append(f"Good lap consistency — {spread}s spread across {len(lap_times)} laps (avg {round(avg_lap)}s).")
        else:
            narratives.append(f"Your laps varied by {spread}s (fastest {min(lap_times)}s, slowest {max(lap_times)}s).")
            tips.append(f"Aim for {round(avg_lap)}s per lap to build more consistency.")

        # Compare lap consistency to route history
        if route_history and latlng_stream:
            # We can't easily compute historical lap consistency without their streams
            # But we can note improvement potential
            if spread > 10:
                tips.append(f"Target your fastest lap time ({min(lap_times)}s) as your consistent pace — you've shown you can do it.")

    # Pace segments for chart
    pace_segments = []
    for seg in segments:
        label = ["Start", "Early", "Mid", "Finish"][seg["segment"] - 1] if len(segments) == 4 else f"Q{seg['segment']}"
        pace_segments.append({
            "label": label,
            "pace": seg["pace"],
            "pace_formatted": _format_pace(seg["pace"]),
        })

    return {
        "narratives": narratives,
        "tips": tips,
        "pacing_pattern": pattern,
        "pace_segments": pace_segments,
        "route_history_count": len(route_history),
        "this_pace": round(this_pace, 1),
        "route_avg_pace": round(sum(r["pace"] for r in route_history) / len(route_history), 1) if route_history else None,
    }
