import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import config
from database import init_db, get_session, async_session
from models import Activity, Split, Stream, BestEffort
from strava_client import StravaClient, STREAM_TYPES
from bulk_import import import_from_export
from best_efforts import compute_and_store_best_efforts, compute_all_best_efforts, TARGET_DISTANCES
from route_matching import group_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

strava = StravaClient()

# In-memory progress tracking for long-running imports
# key: job_id (str), value: dict with status, imported, total, etc.
import_progress: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await strava.close()


app = FastAPI(title="RunFlow", lifespan=lifespan)

# CORS — allow local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://runflow.skdev.one",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RUNNING_SPORT_TYPES = {"Run", "TrailRun", "VirtualRun"}


def _is_running(sport_type: str | None) -> bool:
    """Return True if sport_type represents a running activity."""
    if not sport_type:
        return False
    return any(rt in sport_type for rt in RUNNING_SPORT_TYPES)


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None


def _activity_to_dict(act: Activity) -> dict[str, Any]:
    return {
        "id": act.id,
        "name": act.name,
        "sport_type": act.sport_type,
        "distance": act.distance,
        "moving_time": act.moving_time,
        "elapsed_time": act.elapsed_time,
        "start_date": act.start_date.isoformat() if act.start_date else None,
        "average_speed": act.average_speed,
        "max_speed": act.max_speed,
        "total_elevation_gain": act.total_elevation_gain,
        "elev_high": act.elev_high,
        "elev_low": act.elev_low,
        "start_latlng": act.start_latlng,
        "end_latlng": act.end_latlng,
        "map_summary_polyline": act.map_summary_polyline,
        "has_detailed_data": act.has_detailed_data,
    }


def _split_to_dict(s: Split) -> dict[str, Any]:
    return {
        "id": s.id,
        "activity_id": s.activity_id,
        "split_number": s.split_number,
        "distance": s.distance,
        "moving_time": s.moving_time,
        "elapsed_time": s.elapsed_time,
        "average_speed": s.average_speed,
        "pace_zone": s.pace_zone,
        "elevation_difference": s.elevation_difference,
        "average_heartrate": s.average_heartrate,
    }


def _stream_to_dict(s: Stream) -> dict[str, Any]:
    return {
        "id": s.id,
        "activity_id": s.activity_id,
        "stream_type": s.stream_type,
        "data": s.data,
    }


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

async def _upsert_activity_summary(session: AsyncSession, data: dict[str, Any]) -> Activity:
    """Insert or update an activity from a Strava summary payload."""
    activity_id = data["id"]
    act = await session.get(Activity, activity_id)
    if act is None:
        act = Activity(id=activity_id)
        session.add(act)

    act.name = data.get("name")
    act.sport_type = data.get("sport_type") or data.get("type")
    act.distance = data.get("distance")
    act.moving_time = data.get("moving_time")
    act.elapsed_time = data.get("elapsed_time")
    act.start_date = _parse_dt(data.get("start_date"))
    act.average_speed = data.get("average_speed")
    act.max_speed = data.get("max_speed")
    act.total_elevation_gain = data.get("total_elevation_gain")
    act.elev_high = data.get("elev_high")
    act.elev_low = data.get("elev_low")
    act.start_latlng = data.get("start_latlng")
    act.end_latlng = data.get("end_latlng")
    summary_polyline = None
    if data.get("map"):
        summary_polyline = data["map"].get("summary_polyline")
    act.map_summary_polyline = summary_polyline
    return act


async def _import_detail_and_streams(session: AsyncSession, act: Activity) -> None:
    """Fetch detail (splits) + streams for a single activity and persist."""
    # Detail (splits)
    try:
        detail = await strava.get_activity_detail(act.id)
    except Exception as exc:
        logger.error("Failed to fetch detail for activity %s: %s", act.id, exc)
        return

    # Update fields that may only appear in detail
    act.elev_high = detail.get("elev_high", act.elev_high)
    act.elev_low = detail.get("elev_low", act.elev_low)

    # Splits (prefer metric)
    splits_data = detail.get("splits_metric") or detail.get("splits_standard") or []
    # Remove old splits for this activity
    existing_splits = await session.execute(
        select(Split).where(Split.activity_id == act.id)
    )
    for old in existing_splits.scalars().all():
        await session.delete(old)

    for idx, sd in enumerate(splits_data):
        split = Split(
            activity_id=act.id,
            split_number=sd.get("split", idx + 1),
            distance=sd.get("distance"),
            moving_time=sd.get("moving_time"),
            elapsed_time=sd.get("elapsed_time"),
            average_speed=sd.get("average_speed"),
            pace_zone=sd.get("pace_zone"),
            elevation_difference=sd.get("elevation_difference"),
            average_heartrate=sd.get("average_heartrate"),
        )
        session.add(split)

    # Streams
    try:
        streams_resp = await strava.get_activity_streams(act.id)
    except Exception as exc:
        logger.warning("Failed to fetch streams for activity %s: %s", act.id, exc)
        streams_resp = []

    # Remove old streams
    existing_streams = await session.execute(
        select(Stream).where(Stream.activity_id == act.id)
    )
    for old in existing_streams.scalars().all():
        await session.delete(old)

    # streams_resp is a list of stream objects from the API
    if isinstance(streams_resp, list):
        for stream_obj in streams_resp:
            stream_type = stream_obj.get("type")
            if stream_type in STREAM_TYPES:
                stream = Stream(
                    activity_id=act.id,
                    stream_type=stream_type,
                    data=stream_obj.get("data"),
                )
                session.add(stream)

    act.has_detailed_data = True

    # Auto-compute best efforts from streams
    try:
        await compute_and_store_best_efforts(session, act.id)
    except Exception as exc:
        logger.warning("Failed to compute best efforts for activity %s: %s", act.id, exc)


# ---------------------------------------------------------------------------
# Background task for API import
# ---------------------------------------------------------------------------

async def _bg_import_all(job_id: str) -> None:
    """Run the full API import in the background, updating import_progress."""
    progress = import_progress[job_id]
    progress["status"] = "running"
    progress["imported"] = 0
    progress["skipped"] = 0
    progress["errors"] = 0
    progress["current_page"] = 0

    page = 1
    per_page = 50

    # First pass: count total activities (we'll discover as we go)
    async with async_session() as session:
        while True:
            try:
                activities = await strava.get_activities(page=page, per_page=per_page)
            except Exception as exc:
                logger.error("Error fetching activities page %d: %s", page, exc)
                progress["errors"] += 1
                break

            if not activities:
                break

            progress["current_page"] = page

            for act_data in activities:
                sport = act_data.get("sport_type") or act_data.get("type") or ""
                if not _is_running(sport):
                    progress["skipped"] += 1
                    continue

                act = await _upsert_activity_summary(session, act_data)
                await _import_detail_and_streams(session, act)
                progress["imported"] += 1

                if progress["imported"] % 10 == 0:
                    await session.commit()
                    logger.info("[bg] Imported %d activities so far...", progress["imported"])

            page += 1

        await session.commit()

    progress["status"] = "completed"


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/url")
async def auth_url():
    """Return the Strava OAuth authorization URL."""
    params = {
        "client_id": config.STRAVA_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "activity:read_all",
    }
    url = f"{config.STRAVA_AUTH_URL}?{urlencode(params)}"
    return {"url": url}


@app.get("/api/auth/callback")
async def auth_callback(code: str = Query(...)):
    """Exchange the OAuth code for tokens and save them."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            config.STRAVA_TOKEN_URL,
            data={
                "client_id": config.STRAVA_CLIENT_ID,
                "client_secret": config.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")
        data = resp.json()

    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")

    config.update_env_tokens(access_token, refresh_token)

    # Update the global strava client tokens
    strava.access_token = access_token
    strava.refresh_token = refresh_token

    return {"message": "Authenticated successfully", "athlete": data.get("athlete", {})}


# ---------------------------------------------------------------------------
# Activity endpoints
# ---------------------------------------------------------------------------

@app.get("/api/activities")
async def list_activities(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """List activities from the local DB with pagination."""
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Activity)
        .order_by(Activity.start_date.desc())
        .offset(offset)
        .limit(per_page)
    )
    activities = result.scalars().all()

    total_result = await session.execute(select(func.count(Activity.id)))
    total = total_result.scalar() or 0

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "activities": [_activity_to_dict(a) for a in activities],
    }


@app.get("/api/activities/{activity_id}")
async def get_activity(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Get a single activity with its splits and streams."""
    result = await session.execute(
        select(Activity)
        .where(Activity.id == activity_id)
        .options(
            selectinload(Activity.splits),
            selectinload(Activity.streams),
            selectinload(Activity.best_efforts),
        )
    )
    act = result.scalar_one_or_none()
    if act is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    data = _activity_to_dict(act)
    data["splits"] = [_split_to_dict(s) for s in act.splits]
    data["streams"] = [_stream_to_dict(s) for s in act.streams]
    data["best_efforts"] = [
        {
            "distance_target": be.distance_target,
            "time_seconds": be.time_seconds,
            "pace_sec_per_km": be.pace_sec_per_km,
        }
        for be in sorted(act.best_efforts, key=lambda x: x.distance_target)
    ]
    return data


# ---------------------------------------------------------------------------
# Best Efforts endpoints
# ---------------------------------------------------------------------------

@app.post("/api/best-efforts/compute-all")
async def compute_all_efforts(session: AsyncSession = Depends(get_session)):
    """Compute best efforts for all activities with streams. Runs synchronously."""
    result = await compute_all_best_efforts(session)
    return result


@app.post("/api/best-efforts/compute/{activity_id}")
async def compute_activity_efforts(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Compute best efforts for a single activity."""
    efforts = await compute_and_store_best_efforts(session, activity_id)
    await session.commit()
    return {"activity_id": activity_id, "efforts": efforts}


@app.get("/api/best-efforts/records")
async def best_effort_records(session: AsyncSession = Depends(get_session)):
    """
    Get all-time and current-phase best efforts for each target distance.
    Current phase = latest phase (most recent consecutive runs with no 14+ day gap).
    """
    # All-time bests
    all_time = {}
    for target in TARGET_DISTANCES:
        result = await session.execute(
            select(BestEffort)
            .where(BestEffort.distance_target == target)
            .order_by(BestEffort.time_seconds.asc())
            .limit(1)
        )
        best = result.scalar_one_or_none()
        if best:
            # Get activity date
            act = await session.get(Activity, best.activity_id)
            all_time[target] = {
                "time_seconds": best.time_seconds,
                "pace_sec_per_km": best.pace_sec_per_km,
                "activity_id": best.activity_id,
                "date": act.start_date.isoformat() if act and act.start_date else None,
                "activity_name": act.name if act else None,
            }

    # Current phase bests: find latest phase
    act_result = await session.execute(
        select(Activity).order_by(Activity.start_date.desc())
    )
    all_acts = act_result.scalars().all()

    current_phase_ids = []
    if all_acts:
        current_phase_ids.append(all_acts[0].id)
        for i in range(1, len(all_acts)):
            if all_acts[i - 1].start_date and all_acts[i].start_date:
                gap = (all_acts[i - 1].start_date - all_acts[i].start_date).days
                if gap <= 14:
                    current_phase_ids.append(all_acts[i].id)
                else:
                    break

    current_phase = {}
    if current_phase_ids:
        for target in TARGET_DISTANCES:
            result = await session.execute(
                select(BestEffort)
                .where(
                    BestEffort.distance_target == target,
                    BestEffort.activity_id.in_(current_phase_ids),
                )
                .order_by(BestEffort.time_seconds.asc())
                .limit(1)
            )
            best = result.scalar_one_or_none()
            if best:
                act = await session.get(Activity, best.activity_id)
                current_phase[target] = {
                    "time_seconds": best.time_seconds,
                    "pace_sec_per_km": best.pace_sec_per_km,
                    "activity_id": best.activity_id,
                    "date": act.start_date.isoformat() if act and act.start_date else None,
                }

    return {
        "all_time": {str(k): v for k, v in all_time.items()},
        "current_phase": {str(k): v for k, v in current_phase.items()},
        "current_phase_runs": len(current_phase_ids),
    }


@app.get("/api/activities/{activity_id}/analysis")
async def activity_analysis(activity_id: int, session: AsyncSession = Depends(get_session)):
    """
    Run analysis: compare this activity's best efforts against all-time and current phase.
    Returns insights like PR detection, percentile, phase comparison.
    """
    act = await session.get(Activity, activity_id)
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")

    # Get this activity's best efforts
    be_result = await session.execute(
        select(BestEffort).where(BestEffort.activity_id == activity_id)
    )
    activity_efforts = {be.distance_target: be for be in be_result.scalars().all()}

    # Find current phase activity IDs (for phase comparison)
    act_result = await session.execute(
        select(Activity).order_by(Activity.start_date.desc())
    )
    all_acts = act_result.scalars().all()
    current_phase_ids = []
    if all_acts:
        current_phase_ids.append(all_acts[0].id)
        for i in range(1, len(all_acts)):
            if all_acts[i - 1].start_date and all_acts[i].start_date:
                gap = (all_acts[i - 1].start_date - all_acts[i].start_date).days
                if gap <= 14:
                    current_phase_ids.append(all_acts[i].id)
                else:
                    break

    # Get all best efforts for percentile calc
    insights = []
    for target in TARGET_DISTANCES:
        if target not in activity_efforts:
            continue

        my_effort = activity_efforts[target]

        # Count how many are slower (for percentile)
        count_result = await session.execute(
            select(func.count(BestEffort.id)).where(BestEffort.distance_target == target)
        )
        total = count_result.scalar() or 0

        slower_result = await session.execute(
            select(func.count(BestEffort.id)).where(
                BestEffort.distance_target == target,
                BestEffort.time_seconds > my_effort.time_seconds,
            )
        )
        slower = slower_result.scalar() or 0
        percentile = round((slower / total) * 100) if total > 0 else 0

        # All-time best
        at_result = await session.execute(
            select(BestEffort)
            .where(BestEffort.distance_target == target)
            .order_by(BestEffort.time_seconds.asc())
            .limit(1)
        )
        all_time_best = at_result.scalar_one_or_none()
        is_pr = all_time_best and my_effort.time_seconds <= all_time_best.time_seconds

        # Current phase best (excluding this activity)
        phase_best_time = None
        if current_phase_ids:
            phase_result = await session.execute(
                select(BestEffort)
                .where(
                    BestEffort.distance_target == target,
                    BestEffort.activity_id.in_(current_phase_ids),
                    BestEffort.activity_id != activity_id,
                )
                .order_by(BestEffort.time_seconds.asc())
                .limit(1)
            )
            phase_best = phase_result.scalar_one_or_none()
            phase_best_time = phase_best.time_seconds if phase_best else None

        insight = {
            "distance": target,
            "time_seconds": my_effort.time_seconds,
            "pace_sec_per_km": my_effort.pace_sec_per_km,
            "percentile": percentile,
            "is_pr": is_pr,
            "all_time_best": all_time_best.time_seconds if all_time_best else None,
            "diff_from_best": round(my_effort.time_seconds - all_time_best.time_seconds, 1) if all_time_best else None,
            "phase_best": phase_best_time,
            "diff_from_phase": round(my_effort.time_seconds - phase_best_time, 1) if phase_best_time else None,
        }
        insights.append(insight)

    # Overall pace comparison
    pace_percentile = None
    if act.average_speed and act.average_speed > 0:
        faster_result = await session.execute(
            select(func.count(Activity.id)).where(
                Activity.average_speed < act.average_speed,
                Activity.distance > 0,
            )
        )
        faster = faster_result.scalar() or 0
        total_acts_result = await session.execute(
            select(func.count(Activity.id)).where(Activity.distance > 0)
        )
        total_acts = total_acts_result.scalar() or 0
        pace_percentile = round((faster / total_acts) * 100) if total_acts > 0 else 0

    return {
        "activity_id": activity_id,
        "best_efforts": insights,
        "pace_percentile": pace_percentile,
    }


@app.get("/api/routes")
async def get_routes(session: AsyncSession = Depends(get_session)):
    """Group activities by route similarity and return route stats."""
    result = await session.execute(
        select(Activity).order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    act_dicts = []
    for a in activities:
        act_dicts.append({
            "id": a.id,
            "name": a.name,
            "start_latlng": a.start_latlng,
            "end_latlng": a.end_latlng,
            "distance": a.distance,
            "moving_time": a.moving_time,
            "average_speed": a.average_speed,
            "start_date": a.start_date.isoformat() if a.start_date else None,
            "total_elevation_gain": a.total_elevation_gain,
        })

    routes = group_routes(act_dicts)
    return {"routes": routes, "total_routes": len(routes)}


@app.get("/api/phases")
async def get_phases(
    gap_days: int = Query(14, ge=3, le=90),
    session: AsyncSession = Depends(get_session),
):
    """
    Detect running phases by finding gaps > gap_days between consecutive runs.
    Returns phases with stats: date range, run count, distance, pace, elevation, frequency.
    """
    result = await session.execute(
        select(Activity).order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    if not activities:
        return {"phases": [], "gap_days": gap_days}

    gap_threshold = timedelta(days=gap_days)
    phases = []
    current_phase: list[Activity] = [activities[0]]

    for i in range(1, len(activities)):
        prev_date = activities[i - 1].start_date
        curr_date = activities[i].start_date
        if prev_date and curr_date and (curr_date - prev_date) > gap_threshold:
            phases.append(current_phase)
            current_phase = []
        current_phase.append(activities[i])

    if current_phase:
        phases.append(current_phase)

    phase_stats = []
    for idx, phase in enumerate(phases):
        valid = [a for a in phase if a.distance and a.distance > 0]
        total_distance = sum(a.distance or 0 for a in valid)
        total_time = sum(a.moving_time or 0 for a in valid)
        total_elevation = sum(a.total_elevation_gain or 0 for a in valid)

        start_date = phase[0].start_date
        end_date = phase[-1].start_date
        duration_days = (end_date - start_date).days + 1 if start_date and end_date else 1
        runs_per_week = len(phase) / max(duration_days / 7, 1)

        avg_pace_sec_per_km = None
        if total_distance > 0 and total_time > 0:
            avg_pace_sec_per_km = total_time / (total_distance / 1000)

        longest_act = max(valid, key=lambda a: a.distance or 0) if valid else None
        longest_run = longest_act.distance if longest_act else 0
        fastest_act = None
        best_pace = None
        if valid:
            fastest_act = max(valid, key=lambda a: a.average_speed or 0)
            if fastest_act.average_speed and fastest_act.average_speed > 0:
                best_pace = 1000 / fastest_act.average_speed

        # Break before this phase (gap from previous phase end)
        break_days = None
        if idx > 0:
            prev_end = phases[idx - 1][-1].start_date
            if prev_end and start_date:
                break_days = (start_date - prev_end).days

        # Activity IDs list for this phase
        activity_ids = [a.id for a in phase]

        phase_stats.append({
            "phase_number": idx + 1,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "duration_days": duration_days,
            "break_before_days": break_days,
            "total_runs": len(phase),
            "total_distance_km": round(total_distance / 1000, 2),
            "total_elevation_m": round(total_elevation, 1),
            "avg_distance_km": round(total_distance / 1000 / len(valid), 2) if valid else 0,
            "avg_pace_sec_per_km": round(avg_pace_sec_per_km, 1) if avg_pace_sec_per_km else None,
            "longest_run_km": round(longest_run / 1000, 2),
            "longest_run_id": longest_act.id if longest_act else None,
            "best_pace_sec_per_km": round(best_pace, 1) if best_pace else None,
            "fastest_run_id": fastest_act.id if fastest_act else None,
            "runs_per_week": round(runs_per_week, 1),
            "total_time_seconds": total_time,
            "activity_ids": activity_ids,
        })

    return {"phases": phase_stats, "gap_days": gap_days, "total_phases": len(phase_stats)}


@app.get("/api/stats/monthly")
async def monthly_stats(session: AsyncSession = Depends(get_session)):
    """Monthly aggregated stats for charts."""
    result = await session.execute(
        select(Activity).order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    months: dict[str, dict[str, Any]] = {}
    for act in activities:
        if not act.start_date:
            continue
        key = act.start_date.strftime("%Y-%m")
        if key not in months:
            months[key] = {
                "month": key,
                "runs": 0,
                "total_distance": 0,
                "total_time": 0,
                "total_elevation": 0,
            }
        m = months[key]
        m["runs"] += 1
        m["total_distance"] += act.distance or 0
        m["total_time"] += act.moving_time or 0
        m["total_elevation"] += act.total_elevation_gain or 0

    result_list = []
    for m in sorted(months.values(), key=lambda x: x["month"]):
        dist_km = m["total_distance"] / 1000
        avg_pace = (m["total_time"] / dist_km) if dist_km > 0 else None
        result_list.append({
            "month": m["month"],
            "runs": m["runs"],
            "distance_km": round(dist_km, 2),
            "avg_pace_sec_per_km": round(avg_pace, 1) if avg_pace else None,
            "elevation_m": round(m["total_elevation"], 1),
        })

    return {"months": result_list}


@app.get("/api/stats/personal-records")
async def personal_records(session: AsyncSession = Depends(get_session)):
    """Best split times across all activities."""
    result = await session.execute(
        select(Split).order_by(Split.moving_time.asc())
    )
    all_splits = result.scalars().all()

    # Group by approximate distance (1km splits)
    # Find best single 1km split
    best_1km = None
    for s in all_splits:
        if s.distance and 900 < s.distance < 1100 and s.moving_time:
            if best_1km is None or s.moving_time < best_1km["time"]:
                best_1km = {
                    "time": s.moving_time,
                    "activity_id": s.activity_id,
                    "split_number": s.split_number,
                }

    # Best activity paces (from activities table)
    act_result = await session.execute(
        select(Activity).where(Activity.average_speed.isnot(None)).order_by(Activity.average_speed.desc())
    )
    all_acts = act_result.scalars().all()

    # Best pace for different distance ranges
    prs = {}
    ranges = [
        ("1km", 800, 1200),
        ("2km", 1800, 2200),
        ("3km", 2800, 3200),
        ("5km", 4500, 5500),
        ("10km", 9000, 11000),
    ]
    for label, lo, hi in ranges:
        candidates = [a for a in all_acts if a.distance and lo <= a.distance <= hi]
        if candidates:
            best = min(candidates, key=lambda a: (a.moving_time or float("inf")))
            pace_sec = (best.moving_time / (best.distance / 1000)) if best.distance and best.moving_time else None
            prs[label] = {
                "distance_km": round(best.distance / 1000, 2) if best.distance else None,
                "time_seconds": best.moving_time,
                "pace_sec_per_km": round(pace_sec, 1) if pace_sec else None,
                "activity_id": best.id,
                "date": best.start_date.isoformat() if best.start_date else None,
                "name": best.name,
            }

    return {
        "best_1km_split": best_1km,
        "personal_records": prs,
    }


@app.get("/api/stats/heatmap")
async def activity_heatmap(session: AsyncSession = Depends(get_session)):
    """Daily run data for the last 12 months (heatmap)."""
    cutoff = datetime.now() - timedelta(days=365)
    result = await session.execute(
        select(Activity)
        .where(Activity.start_date >= cutoff)
        .order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    days: dict[str, dict] = {}
    for act in activities:
        if not act.start_date:
            continue
        key = act.start_date.strftime("%Y-%m-%d")
        if key not in days:
            days[key] = {"date": key, "runs": 0, "distance": 0}
        days[key]["runs"] += 1
        days[key]["distance"] += act.distance or 0

    day_list = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        day_list.append({
            "date": d["date"],
            "runs": d["runs"],
            "distance_km": round(d["distance"] / 1000, 2),
        })

    return {"days": day_list}


@app.delete("/api/activities/{activity_id}")
async def delete_activity(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Delete an activity from the local DB (does NOT delete from Strava)."""
    act = await session.get(Activity, activity_id)
    if act is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    await session.execute(sa_delete(Split).where(Split.activity_id == activity_id))
    await session.execute(sa_delete(Stream).where(Stream.activity_id == activity_id))
    await session.delete(act)
    await session.commit()
    return {"message": f"Activity {activity_id} deleted", "id": activity_id}


# ---------------------------------------------------------------------------
# Date-based import
# ---------------------------------------------------------------------------

class DateImportRequest(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD


@app.post("/api/import/by-date")
async def import_by_date(req: DateImportRequest, session: AsyncSession = Depends(get_session)):
    """
    Import running activities from Strava for a specific date range.
    Uses the Strava API after/before epoch params so we only fetch what's needed.
    """
    try:
        start_dt = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(req.end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    after_epoch = int(start_dt.timestamp())
    before_epoch = int(end_dt.timestamp())

    imported = 0
    skipped = 0
    page = 1
    per_page = 50

    while True:
        try:
            activities = await strava.get_activities(
                page=page, per_page=per_page, after=after_epoch, before=before_epoch
            )
        except Exception as exc:
            logger.error("Error fetching activities page %d: %s", page, exc)
            break

        if not activities:
            break

        already_existed = 0
        for act_data in activities:
            sport = act_data.get("sport_type") or act_data.get("type") or ""
            if not _is_running(sport):
                skipped += 1
                continue

            # Skip if already imported with detail
            activity_id = act_data["id"]
            existing = await session.get(Activity, activity_id)
            if existing and existing.has_detailed_data:
                already_existed += 1
                continue

            act = await _upsert_activity_summary(session, act_data)
            await _import_detail_and_streams(session, act)
            imported += 1

        await session.commit()
        page += 1

    return {
        "imported": imported,
        "already_existed": already_existed,
        "skipped_non_running": skipped,
        "start_date": req.start_date,
        "end_date": req.end_date,
    }


# ---------------------------------------------------------------------------
# Import endpoints
# ---------------------------------------------------------------------------

@app.post("/api/import/all")
async def import_all(background_tasks: BackgroundTasks):
    """
    Bulk import: fetch ALL running activities from Strava API.
    Runs as a background task — returns a job_id to poll for progress.
    """
    job_id = str(uuid.uuid4())
    import_progress[job_id] = {
        "status": "starting",
        "type": "api",
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "current_page": 0,
    }

    background_tasks.add_task(_bg_import_all, job_id)

    return {
        "message": "Import started in background",
        "job_id": job_id,
    }


@app.post("/api/import/sync")
async def import_sync(session: AsyncSession = Depends(get_session)):
    """
    Incremental sync: import only activities that are not yet in the DB.
    Stops paginating once it finds a page where all activities already exist.
    """
    imported = 0
    skipped = 0
    page = 1
    per_page = 50

    while True:
        try:
            activities = await strava.get_activities(page=page, per_page=per_page)
        except Exception as exc:
            logger.error("Error fetching activities page %d: %s", page, exc)
            break

        if not activities:
            break

        all_exist = True
        for act_data in activities:
            sport = act_data.get("sport_type") or act_data.get("type") or ""
            if not _is_running(sport):
                skipped += 1
                continue

            activity_id = act_data["id"]
            existing = await session.get(Activity, activity_id)
            if existing and existing.has_detailed_data:
                continue

            all_exist = False
            act = await _upsert_activity_summary(session, act_data)
            await _import_detail_and_streams(session, act)
            imported += 1

            if imported % 10 == 0:
                await session.commit()

        await session.commit()

        if all_exist:
            # All activities on this page already imported — we're caught up
            break

        page += 1

    return {
        "imported": imported,
        "skipped_non_running": skipped,
    }


class BulkImportRequest(BaseModel):
    directory: str


@app.post("/api/import/bulk")
async def import_bulk(req: BulkImportRequest, background_tasks: BackgroundTasks):
    """
    Import running activities from a Strava data export (ZIP or extracted directory).
    Runs as a background task — returns a job_id to poll for progress.
    """
    import os
    path = req.directory

    # Basic validation
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"Path does not exist: {path}")

    job_id = str(uuid.uuid4())
    import_progress[job_id] = {
        "status": "starting",
        "type": "bulk",
        "imported": 0,
        "total": 0,
        "current": 0,
    }

    async def _bg_bulk_import():
        progress = import_progress[job_id]
        try:
            async with async_session() as session:
                result = await import_from_export(session, path, progress=progress)
                progress.update(result)
                progress["status"] = "completed"
        except FileNotFoundError as exc:
            progress["status"] = "error"
            progress["error"] = str(exc)
        except Exception as exc:
            logger.error("Bulk import failed: %s", exc, exc_info=True)
            progress["status"] = "error"
            progress["error"] = str(exc)

    background_tasks.add_task(_bg_bulk_import)

    return {
        "message": "Bulk import started in background",
        "job_id": job_id,
    }


@app.get("/api/import/status")
async def import_status(
    job_id: str = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """
    Return import status. If job_id is provided, return progress for that
    specific background job. Otherwise, return general DB counts.
    """
    # General DB counts (always returned)
    total_result = await session.execute(select(func.count(Activity.id)))
    total = total_result.scalar() or 0

    detailed_result = await session.execute(
        select(func.count(Activity.id)).where(Activity.has_detailed_data.is_(True))
    )
    detailed = detailed_result.scalar() or 0

    response: dict[str, Any] = {
        "total_activities": total,
        "with_detailed_data": detailed,
    }

    if job_id:
        job = import_progress.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        response["job"] = {
            "job_id": job_id,
            **job,
        }

    # Also return any active jobs
    active_jobs = {
        k: v for k, v in import_progress.items()
        if v.get("status") in ("starting", "running")
    }
    if active_jobs:
        response["active_jobs"] = [
            {"job_id": k, **v} for k, v in active_jobs.items()
        ]

    return response
