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

logger = logging.getLogger(__name__)

TARGET_DISTANCES = [100, 200, 400, 500, 1000, 2000]

# Minimum realistic times (seconds) to filter GPS glitches.
# Based on ~2:30/km pace (very fast amateur sprint) as floor.
MIN_TIMES = {100: 12, 200: 25, 400: 55, 500: 70, 1000: 150, 2000: 300}


def compute_best_efforts_from_streams(
    distance_stream: list[float],
    time_stream: list[int],
) -> list[dict[str, Any]]:
    """
    Given distance and time streams, find the fastest segment for each target distance.

    Uses a two-pointer sliding window: for each target distance, scan through the
    stream and find the window where (distance[j] - distance[i]) >= target with
    the minimum (time[j] - time[i]).
    """
    if not distance_stream or not time_stream or len(distance_stream) < 2:
        return []

    total_distance = distance_stream[-1]
    results = []

    for target in TARGET_DISTANCES:
        if total_distance < target * 0.9:
            # Run is too short for this target
            continue

        best_time = float("inf")
        best_start = 0
        best_end = 0

        i = 0
        for j in range(1, len(distance_stream)):
            segment_dist = distance_stream[j] - distance_stream[i]

            # Move start pointer forward while segment is longer than needed
            while i < j and (distance_stream[j] - distance_stream[i + 1]) >= target:
                i += 1
                segment_dist = distance_stream[j] - distance_stream[i]

            if segment_dist >= target:
                segment_time = time_stream[j] - time_stream[i]
                if segment_time > 0 and segment_time < best_time:
                    best_time = segment_time
                    best_start = i
                    best_end = j

        min_time = MIN_TIMES.get(target, 10)
        if best_time < float("inf") and best_time >= min_time:
            pace = best_time / (target / 1000)
            results.append({
                "distance_target": target,
                "time_seconds": round(best_time, 1),
                "pace_sec_per_km": round(pace, 1),
                "start_index": best_start,
                "end_index": best_end,
            })

    return results


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
    efforts = compute_best_efforts_from_streams(distance_stream, time_stream)

    if not efforts:
        return []

    # Delete old efforts for this activity
    await session.execute(
        sa_delete(BestEffort).where(BestEffort.activity_id == activity_id)
    )

    # Store new efforts
    for e in efforts:
        be = BestEffort(
            activity_id=activity_id,
            distance_target=e["distance_target"],
            time_seconds=e["time_seconds"],
            pace_sec_per_km=e["pace_sec_per_km"],
            start_index=e["start_index"],
            end_index=e["end_index"],
        )
        session.add(be)

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
