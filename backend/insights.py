"""
Run insights: generate narrative analysis comparing this run segment-by-segment
to recent runs on the same route.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, Stream
from laps import detect_laps
from route_matching import _points_close, _distance_similar

logger = logging.getLogger(__name__)

SEGMENT_LABELS = ["Start", "Early", "Mid", "Finish"]


def _fp(sec_per_km: float) -> str:
    """Format pace as M:SS."""
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


async def _get_route_history(session, act, limit=5):
    """Find recent similar runs on the same route, with their streams."""
    if not act.start_latlng:
        return []

    history_result = await session.execute(
        select(Activity)
        .where(
            Activity.id != act.id,
            Activity.distance > 500,
            Activity.start_latlng.isnot(None),
            Activity.has_detailed_data.is_(True),
            Activity.is_interval.isnot(True),
        )
        .order_by(Activity.start_date.desc())
    )
    all_activities = history_result.scalars().all()

    matches = []
    for other in all_activities:
        if not other.start_latlng or not other.distance or not other.moving_time:
            continue
        if (_points_close(act.start_latlng, other.start_latlng, 300)
                and _distance_similar(act.distance, other.distance, 0.25)):
            matches.append(other)
        if len(matches) >= limit:
            break

    return matches


async def _get_history_segments(session, activity_ids):
    """Compute pace segments for historical runs."""
    all_segments = []
    for aid in activity_ids:
        result = await session.execute(
            select(Stream).where(
                Stream.activity_id == aid,
                Stream.stream_type.in_(["distance", "time"]),
            )
        )
        streams = {s.stream_type: s.data for s in result.scalars().all()}
        d = streams.get("distance")
        t = streams.get("time")
        if d and t:
            segs = _compute_pace_segments(d, t)
            if len(segs) == 4:
                all_segments.append(segs)
    return all_segments


async def generate_run_insight(
    session: AsyncSession,
    activity_id: int,
) -> dict[str, Any] | None:
    """Generate insights comparing this run to recent runs on the same route."""
    act = await session.get(Activity, activity_id)
    if not act or not act.distance or act.distance < 200:
        return None

    # Get streams
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

    this_pace = act.moving_time / (act.distance / 1000) if act.distance > 0 else 0
    segments = _compute_pace_segments(dist_stream, time_stream)

    # Get laps
    this_laps = None
    if latlng_stream:
        this_laps = detect_laps(latlng_stream, dist_stream, time_stream)

    # Get recent route history (last 5 similar runs)
    route_runs = await _get_route_history(session, act, limit=5)
    recent_count = len(route_runs)

    # Get segment-by-segment history for comparison
    history_segments = []
    recent_paces = []
    if route_runs:
        history_segments = await _get_history_segments(session, [r.id for r in route_runs])
        recent_paces = [r.moving_time / (r.distance / 1000) for r in route_runs if r.distance > 0]

    narratives = []
    tips = []

    # --- Overall pace vs recent ---
    if recent_paces:
        recent_avg = sum(recent_paces) / len(recent_paces)
        recent_best = min(recent_paces)
        diff = this_pace - recent_avg

        if diff < -5:
            narratives.append(
                f"Faster than your recent runs! You ran {_fp(this_pace)}/km — "
                f"{abs(round(diff))}s faster than your last {recent_count} runs' average "
                f"of {_fp(recent_avg)}/km on this route."
            )
        elif diff > 10:
            narratives.append(
                f"Took it easier today — {_fp(this_pace)}/km vs your recent average "
                f"of {_fp(recent_avg)}/km ({round(diff)}s slower). "
                f"Recovery runs are important too."
            )
        else:
            narratives.append(
                f"Consistent effort — {_fp(this_pace)}/km, right in line with your "
                f"recent {_fp(recent_avg)}/km average on this route."
            )

        # PR check
        if this_pace <= recent_best:
            narratives.append(
                f"This is your fastest recent run on this route! "
                f"Previous best was {_fp(recent_best)}/km."
            )
    else:
        narratives.append(
            f"First tracked run on this route at {_fp(this_pace)}/km. "
            f"Run it again to start tracking your progress here."
        )

    # --- Segment-by-segment comparison ---
    if segments and len(segments) == 4 and history_segments:
        # Average each segment across history
        avg_hist_segs = []
        for seg_idx in range(4):
            hist_paces = [h[seg_idx]["pace"] for h in history_segments if len(h) > seg_idx]
            avg_hist_segs.append(sum(hist_paces) / len(hist_paces) if hist_paces else 0)

        seg_narratives = []
        for i, (this_seg, hist_avg) in enumerate(zip(segments, avg_hist_segs)):
            if hist_avg == 0:
                continue
            diff = this_seg["pace"] - hist_avg
            label = SEGMENT_LABELS[i]

            if diff < -8:
                seg_narratives.append(f"started {abs(round(diff))}s/km faster than usual" if i == 0
                    else f"pushed harder in the {label.lower()} section ({abs(round(diff))}s faster)")
            elif diff > 8:
                seg_narratives.append(f"started slower than usual (+{round(diff)}s)" if i == 0
                    else f"slowed in the {label.lower()} section (+{round(diff)}s)")

        if seg_narratives:
            narratives.append("Compared to your recent runs: you " + ", ".join(seg_narratives) + ".")

        # Specific tip based on segment comparison
        start_diff = segments[0]["pace"] - avg_hist_segs[0]
        finish_diff = segments[3]["pace"] - avg_hist_segs[3]

        if start_diff < -10 and finish_diff > 5:
            tips.append(
                f"You went out fast ({_fp(segments[0]['pace'])}/km) but faded at the end "
                f"({_fp(segments[3]['pace'])}/km). Try starting closer to {_fp(avg_hist_segs[0])}/km."
            )
        elif start_diff > 5 and finish_diff < -5:
            tips.append("Great negative split — you built pace through the run. Strong execution.")
        elif all(abs(segments[i]["pace"] - avg_hist_segs[i]) < 5 for i in range(4)):
            tips.append("Very consistent with your recent pacing pattern. To improve, try pushing the mid section slightly.")

    # --- Pacing pattern ---
    if segments:
        paces = [s["pace"] for s in segments]
        first_half = sum(paces[:2]) / 2
        second_half = sum(paces[2:]) / 2
        split_diff = second_half - first_half

        if split_diff < -10:
            pattern = "negative_split"
        elif split_diff > 15:
            pattern = "fade"
        elif split_diff > 5:
            pattern = "slight_fade"
        else:
            pattern = "even"
    else:
        pattern = "unknown"

    # --- Lap insights ---
    if this_laps and this_laps.get("lap_count", 0) >= 3:
        lap_times = [l["duration_s"] for l in this_laps["laps"]]
        spread = max(lap_times) - min(lap_times)
        avg_lap = sum(lap_times) / len(lap_times)

        # Compare first half vs second half laps
        mid = len(lap_times) // 2
        first_half_avg = sum(lap_times[:mid]) / mid
        second_half_avg = sum(lap_times[mid:]) / (len(lap_times) - mid)
        lap_diff = second_half_avg - first_half_avg

        if spread <= 5:
            narratives.append(f"Incredibly even laps — only {spread}s spread across {len(lap_times)} laps.")
        elif spread <= 12:
            if lap_diff > 3:
                narratives.append(
                    f"Your later laps were {round(lap_diff)}s slower on average "
                    f"({round(first_half_avg)}s → {round(second_half_avg)}s per lap)."
                )
            elif lap_diff < -3:
                narratives.append(
                    f"Strong negative split — your later laps were {abs(round(lap_diff))}s faster "
                    f"({round(first_half_avg)}s → {round(second_half_avg)}s per lap)."
                )
            else:
                narratives.append(f"Good lap consistency — {spread}s spread across {len(lap_times)} laps.")
        else:
            narratives.append(
                f"Lap times varied by {spread}s (best {min(lap_times)}s, worst {max(lap_times)}s)."
            )
            tips.append(f"Try targeting {round(avg_lap)}s per lap. Your best lap shows you can run {min(lap_times)}s.")

    # Build pace segments for chart
    pace_segments = []
    for i, seg in enumerate(segments):
        label = SEGMENT_LABELS[i] if i < 4 else f"Q{i+1}"
        entry = {
            "label": label,
            "pace": seg["pace"],
            "pace_formatted": _fp(seg["pace"]),
        }
        # Add history avg for comparison
        if history_segments and i < len(history_segments[0]):
            hist_paces = [h[i]["pace"] for h in history_segments if len(h) > i]
            entry["history_avg"] = round(sum(hist_paces) / len(hist_paces), 1) if hist_paces else None
            entry["history_formatted"] = _fp(entry["history_avg"]) if entry.get("history_avg") else None
        pace_segments.append(entry)

    return {
        "narratives": narratives,
        "tips": tips,
        "pacing_pattern": pattern,
        "pace_segments": pace_segments,
        "route_history_count": recent_count,
        "this_pace": round(this_pace, 1),
        "recent_avg_pace": round(sum(recent_paces) / len(recent_paces), 1) if recent_paces else None,
    }
