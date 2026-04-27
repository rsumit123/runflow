"""
Compute best efforts (fastest time for target distances) from GPS streams.

Uses a sliding window over the distance/time streams to find the fastest
segment for each target distance (200m, 400m, 500m, 1000m, 2000m).
"""

import logging
from typing import Any

from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, Stream, BestEffort
from sqlalchemy import select as sa_select

logger = logging.getLogger(__name__)

TARGET_DISTANCES = [100, 200, 400, 500, 1000, 2000]

# Minimum realistic times (seconds) to filter GPS glitches.
# Based on world records (rounded down slightly to avoid filtering genuine fast efforts).
# 100m: 9.58s (Bolt), 200m: 19.19s, 400m: 43.03s, 500m: ~55s, 1000m: 2:11, 2000m: 4:44
MIN_TIMES = {100: 9, 200: 18, 400: 42, 500: 54, 1000: 130, 2000: 280}


def _has_gps_glitch(distance_stream, time_stream, start_idx, end_idx, max_speed_mps=9) -> bool:
    """
    Check if a segment contains GPS glitches.
    A glitch = any single point-to-point speed exceeding max_speed_mps (9 m/s ≈ 1:51/km, 32 km/h).
    """
    for k in range(start_idx, end_idx):
        d = distance_stream[k + 1] - distance_stream[k]
        t = time_stream[k + 1] - time_stream[k]
        if t <= 0:
            continue
        speed = d / t
        if speed > max_speed_mps:
            return True
    return False


def compute_best_efforts_from_streams(
    distance_stream: list[float],
    time_stream: list[int],
) -> list[dict[str, Any]]:
    """
    Given distance and time streams, find the fastest segment for each target distance.

    Uses a two-pointer sliding window: for each target distance, scan through the
    stream and find the window where (distance[j] - distance[i]) >= target with
    the minimum (time[j] - time[i]).

    Rejects segments containing GPS glitches (point-to-point speed > 9 m/s).
    """
    if not distance_stream or not time_stream or len(distance_stream) < 2:
        return []

    total_distance = distance_stream[-1]
    results = []
    glitch_count = 0

    # Count total GPS glitches in the run
    for k in range(len(distance_stream) - 1):
        d = distance_stream[k + 1] - distance_stream[k]
        t = time_stream[k + 1] - time_stream[k]
        if t > 0 and d / t > 9:
            glitch_count += 1

    for target in TARGET_DISTANCES:
        if total_distance < target * 0.9:
            continue

        best_time = float("inf")
        best_start = 0
        best_end = 0
        had_glitch_candidate = False

        i = 0
        for j in range(1, len(distance_stream)):
            segment_dist = distance_stream[j] - distance_stream[i]

            while i < j and (distance_stream[j] - distance_stream[i + 1]) >= target:
                i += 1
                segment_dist = distance_stream[j] - distance_stream[i]

            if segment_dist >= target:
                segment_time = time_stream[j] - time_stream[i]
                if segment_time > 0 and segment_time < best_time:
                    if not _has_gps_glitch(distance_stream, time_stream, i, j):
                        best_time = segment_time
                        best_start = i
                        best_end = j
                    else:
                        had_glitch_candidate = True

        min_time = MIN_TIMES.get(target, 10)
        if best_time < float("inf") and best_time >= min_time:
            pace = best_time / (target / 1000)
            results.append({
                "distance_target": target,
                "time_seconds": round(best_time, 1),
                "pace_sec_per_km": round(pace, 1),
                "start_index": best_start,
                "end_index": best_end,
                "glitch_filtered": had_glitch_candidate,
            })

    return {"efforts": results, "gps_glitch_count": glitch_count}


async def compute_and_store_best_efforts(
    session: AsyncSession,
    activity_id: int,
) -> list[dict[str, Any]]:
    """
    Compute best efforts for a single activity and store in DB.
    Returns the computed efforts.
    """
    # Get distance and time streams
    result = await session.execute(
        select(Stream).where(
            Stream.activity_id == activity_id,
            Stream.stream_type.in_(["distance", "time"]),
        )
    )
    streams = {s.stream_type: s.data for s in result.scalars().all()}

    distance_stream = streams.get("distance")
    time_stream = streams.get("time")

    if not distance_stream or not time_stream:
        return []

    # Compute
    result_data = compute_best_efforts_from_streams(distance_stream, time_stream)
    efforts = result_data["efforts"] if isinstance(result_data, dict) else result_data
    gps_glitch_count = result_data.get("gps_glitch_count", 0) if isinstance(result_data, dict) else 0

    if not efforts:
        return []

    # Get total run distance to determine if effort is "dedicated"
    total_distance = distance_stream[-1] if distance_stream else 0

    # Delete old efforts for this activity
    await session.execute(
        sa_delete(BestEffort).where(BestEffort.activity_id == activity_id)
    )

    # Store new efforts
    for e in efforts:
        # Dedicated = total run distance < 2x the effort target distance
        is_dedicated = total_distance < (e["distance_target"] * 2) if total_distance > 0 else False
        be = BestEffort(
            activity_id=activity_id,
            distance_target=e["distance_target"],
            time_seconds=e["time_seconds"],
            pace_sec_per_km=e["pace_sec_per_km"],
            start_index=e["start_index"],
            end_index=e["end_index"],
            is_dedicated=is_dedicated,
        )
        session.add(be)
        e["is_dedicated"] = is_dedicated

    return efforts


async def compute_all_best_efforts(
    session: AsyncSession,
    progress: dict[str, Any] | None = None,
) -> dict[str, int]:
    """
    Compute best efforts for all activities that have streams but no best efforts yet.
    """
    # Find activities with streams
    result = await session.execute(
        select(Activity.id).where(Activity.has_detailed_data.is_(True))
    )
    activity_ids = [r[0] for r in result.all()]

    # Find which already have best efforts
    existing_result = await session.execute(
        select(BestEffort.activity_id).distinct()
    )
    existing_ids = {r[0] for r in existing_result.all()}

    to_process = [aid for aid in activity_ids if aid not in existing_ids]
    total = len(to_process)
    computed = 0
    skipped = 0

    if progress:
        progress["total"] = total
        progress["computed"] = 0
        progress["status"] = "running"

    for idx, activity_id in enumerate(to_process):
        efforts = await compute_and_store_best_efforts(session, activity_id)
        if efforts:
            computed += 1
        else:
            skipped += 1

        if (idx + 1) % 20 == 0:
            await session.commit()

        if progress:
            progress["computed"] = computed
            progress["current"] = idx + 1

    await session.commit()

    if progress:
        progress["status"] = "completed"

    return {"computed": computed, "skipped": skipped, "total": total}
