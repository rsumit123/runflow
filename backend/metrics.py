"""
Run quality metrics: consistency, fade, decay.
Computed per run from laps (regular) or interval config (intervals).
"""

import math
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, Stream
from laps import detect_laps

logger = logging.getLogger(__name__)


def _compute_cv(values: list[float]) -> float:
    """Coefficient of variation (0-1). Lower = more consistent."""
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean


def _cv_to_score(cv: float) -> int:
    """Convert CV to 0-100 score. Lower CV = higher score."""
    if cv <= 0.02:
        return 98
    elif cv <= 0.03:
        return 90
    elif cv <= 0.05:
        return 80
    elif cv <= 0.08:
        return 65
    elif cv <= 0.12:
        return 50
    elif cv <= 0.18:
        return 35
    else:
        return max(10, int(35 - (cv - 0.18) * 100))


def compute_interval_metrics(interval_config: dict) -> dict[str, Any] | None:
    """Compute metrics from saved interval config."""
    result = interval_config.get("result")
    if not result or not result.get("is_interval"):
        return None

    reps = [s for s in result.get("segments", []) if s.get("type") == "rep"]
    if len(reps) < 2:
        return None

    times = [r["duration_s"] for r in reps if r.get("duration_s")]
    if len(times) < 2:
        return None

    # Consistency (CV of rep times)
    cv = _compute_cv(times)
    score = _cv_to_score(cv)

    # Fade (second half avg - first half avg in seconds)
    mid = len(times) // 2
    first_half = sum(times[:mid]) / mid
    second_half = sum(times[mid:]) / (len(times) - mid)
    fade = round(second_half - first_half, 1)

    # Decay (seconds added per rep, linear regression slope)
    n = len(times)
    x_mean = (n - 1) / 2
    y_mean = sum(times) / n
    numerator = sum((i - x_mean) * (times[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    decay = round(numerator / denominator, 1) if denominator > 0 else 0

    return {
        "type": "interval",
        "consistency": score,
        "fade_s": fade,
        "decay_s_per_rep": decay,
        "rep_count": len(times),
        "avg_time": round(sum(times) / len(times), 1),
    }


def compute_lap_metrics(laps_data: dict) -> dict[str, Any] | None:
    """Compute metrics from lap detection results."""
    if not laps_data or laps_data.get("lap_count", 0) < 3:
        return None

    times = [l["duration_s"] for l in laps_data["laps"] if l.get("duration_s")]
    if len(times) < 3:
        return None

    cv = _compute_cv(times)
    score = _cv_to_score(cv)

    mid = len(times) // 2
    first_half = sum(times[:mid]) / mid
    second_half = sum(times[mid:]) / (len(times) - mid)
    fade = round(second_half - first_half, 1)

    n = len(times)
    x_mean = (n - 1) / 2
    y_mean = sum(times) / n
    numerator = sum((i - x_mean) * (times[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    decay = round(numerator / denominator, 1) if denominator > 0 else 0

    return {
        "type": "regular",
        "consistency": score,
        "fade_s": fade,
        "decay_s_per_lap": decay,
        "lap_count": len(times),
        "avg_time": round(sum(times) / len(times), 1),
    }


async def get_metrics_trend(session: AsyncSession) -> dict[str, Any]:
    """Compute metrics for all recent runs and return as trend data."""
    # Get recent activities with streams or interval configs
    result = await session.execute(
        select(Activity)
        .where(Activity.has_detailed_data.is_(True))
        .order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    regular_trend = []
    interval_trend = []

    for act in activities:
        if not act.start_date:
            continue

        date_str = act.start_date.strftime("%Y-%m-%d")

        if act.is_interval and act.interval_config:
            metrics = compute_interval_metrics(act.interval_config)
            if metrics:
                interval_trend.append({
                    "date": date_str,
                    "activity_id": act.id,
                    **metrics,
                })
        else:
            # Try to compute laps for this activity
            stream_result = await session.execute(
                select(Stream).where(
                    Stream.activity_id == act.id,
                    Stream.stream_type.in_(["latlng", "distance", "time"]),
                )
            )
            streams = {s.stream_type: s.data for s in stream_result.scalars().all()}
            latlng = streams.get("latlng")
            dist = streams.get("distance")
            time = streams.get("time")

            if latlng and dist and time:
                laps = detect_laps(latlng, dist, time)
                if laps and laps.get("lap_count", 0) >= 3:
                    metrics = compute_lap_metrics(laps)
                    if metrics:
                        regular_trend.append({
                            "date": date_str,
                            "activity_id": act.id,
                            **metrics,
                        })

    return {
        "regular": regular_trend,
        "interval": interval_trend,
    }
