"""
Goal recommendation engine.
Analyzes running history to suggest realistic targets.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, BestEffort
from best_efforts import TARGET_DISTANCES

logger = logging.getLogger(__name__)


async def recommend_speed_goal(
    session: AsyncSession,
    distance: int,
) -> dict[str, Any]:
    """
    Recommend a speed goal for a given distance.
    Analyzes all-time best, current phase best, and improvement trend.
    """
    # All-time best
    at_result = await session.execute(
        select(BestEffort)
        .where(BestEffort.distance_target == distance)
        .order_by(BestEffort.time_seconds.asc())
        .limit(1)
    )
    all_time_best = at_result.scalar_one_or_none()

    if not all_time_best:
        return {
            "distance": distance,
            "has_data": False,
            "message": f"No {distance}m efforts recorded yet. Run more to get a recommendation!",
        }

    # Get all efforts for this distance, sorted by date
    efforts_result = await session.execute(
        select(BestEffort, Activity.start_date)
        .join(Activity, BestEffort.activity_id == Activity.id)
        .where(BestEffort.distance_target == distance)
        .order_by(Activity.start_date.asc())
    )
    all_efforts = [(be, date) for be, date in efforts_result.all()]

    # Current phase best (last 14 days of consecutive running)
    act_result = await session.execute(
        select(Activity).order_by(Activity.start_date.desc())
    )
    all_acts = act_result.scalars().all()
    phase_ids = set()
    if all_acts:
        phase_ids.add(all_acts[0].id)
        for i in range(1, len(all_acts)):
            if all_acts[i - 1].start_date and all_acts[i].start_date:
                gap = (all_acts[i - 1].start_date - all_acts[i].start_date).days
                if gap <= 14:
                    phase_ids.add(all_acts[i].id)
                else:
                    break

    phase_best = None
    for be, date in all_efforts:
        if be.activity_id in phase_ids:
            if phase_best is None or be.time_seconds < phase_best.time_seconds:
                phase_best = be

    # Trend: compare recent half vs older half
    trend_sec_per_phase = 0
    if len(all_efforts) >= 4:
        mid = len(all_efforts) // 2
        older_times = [be.time_seconds for be, _ in all_efforts[:mid]]
        recent_times = [be.time_seconds for be, _ in all_efforts[mid:]]
        older_avg = sum(older_times) / len(older_times)
        recent_avg = sum(recent_times) / len(recent_times)
        trend_sec_per_phase = round(older_avg - recent_avg, 1)  # positive = improving

    # Calculate recommended target
    current_best = phase_best.time_seconds if phase_best else all_time_best.time_seconds
    at_best = all_time_best.time_seconds

    if current_best <= at_best:
        # Already at all-time best — suggest 3-5% improvement
        improvement = max(2, round(current_best * 0.04))
        recommended = round(current_best - improvement, 1)
    elif trend_sec_per_phase > 2:
        # Improving — suggest slightly better than current
        improvement = max(2, min(round(trend_sec_per_phase * 0.8), 10))
        recommended = round(current_best - improvement, 1)
    else:
        # Not improving or declining — suggest matching all-time best
        recommended = at_best

    # Compute percentiles for context
    all_times = [be.time_seconds for be, _ in all_efforts]
    total = len(all_times)
    faster_count = sum(1 for t in all_times if t <= current_best)
    current_percentile = round((faster_count / total) * 100) if total > 0 else 0

    # Recent 5 efforts
    recent_5 = []
    for be, date in all_efforts[-5:]:
        recent_5.append({
            "time_seconds": be.time_seconds,
            "date": date.isoformat() if date else None,
            "activity_id": be.activity_id,
        })

    return {
        "distance": distance,
        "has_data": True,
        "all_time_best": at_best,
        "all_time_best_id": all_time_best.activity_id,
        "current_phase_best": phase_best.time_seconds if phase_best else None,
        "trend_per_phase": trend_sec_per_phase,
        "trend_direction": "improving" if trend_sec_per_phase > 2 else "declining" if trend_sec_per_phase < -2 else "steady",
        "recommended_target": recommended,
        "current_percentile": current_percentile,
        "total_efforts": total,
        "recent_efforts": recent_5,
    }


async def recommend_consistency_goal(session: AsyncSession) -> dict[str, Any]:
    """Recommend a weekly run frequency goal."""
    # Get runs per week for the last 12 weeks
    cutoff = datetime.now() - timedelta(weeks=12)
    result = await session.execute(
        select(Activity).where(Activity.start_date >= cutoff).order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    if not activities:
        return {"has_data": False, "message": "Not enough recent data"}

    # Count runs per week
    weekly_counts = {}
    for a in activities:
        if a.start_date:
            week_key = a.start_date.isocalendar()[:2]
            weekly_counts[week_key] = weekly_counts.get(week_key, 0) + 1

    counts = list(weekly_counts.values())
    current_avg = round(sum(counts) / len(counts), 1) if counts else 0
    best_week = max(counts) if counts else 0

    # Recommend 1 more than current average, capped at best week
    recommended = min(round(current_avg) + 1, best_week, 7)
    recommended = max(recommended, 2)

    return {
        "has_data": True,
        "current_avg_per_week": current_avg,
        "best_week": best_week,
        "weeks_tracked": len(counts),
        "recommended_target": recommended,
    }


async def recommend_volume_goal(session: AsyncSession) -> dict[str, Any]:
    """Recommend a weekly distance goal."""
    cutoff = datetime.now() - timedelta(weeks=12)
    result = await session.execute(
        select(Activity).where(Activity.start_date >= cutoff).order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    if not activities:
        return {"has_data": False, "message": "Not enough recent data"}

    weekly_km = {}
    for a in activities:
        if a.start_date and a.distance:
            week_key = a.start_date.isocalendar()[:2]
            weekly_km[week_key] = weekly_km.get(week_key, 0) + a.distance / 1000

    kms = list(weekly_km.values())
    current_avg = round(sum(kms) / len(kms), 1) if kms else 0
    best_week = round(max(kms), 1) if kms else 0

    # Recommend 10-15% above current average
    recommended = round(current_avg * 1.12, 1)
    recommended = max(recommended, 5)

    return {
        "has_data": True,
        "current_avg_km_per_week": current_avg,
        "best_week_km": best_week,
        "weeks_tracked": len(kms),
        "recommended_target": recommended,
    }
