import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import config
from database import init_db, get_session, async_session
from models import (Activity, Split, Stream, BestEffort, RouteLabel, RouteMerge, Goal, Plan,
                    PlannedWorkout, DailyWellness, ChatMessage)
from strava_client import StravaClient, STREAM_TYPES
from bulk_import import import_from_export, encode_polyline
from best_efforts import compute_and_store_best_efforts, compute_all_best_efforts, TARGET_DISTANCES
from goals import recommend_speed_goal, recommend_consistency_goal, recommend_volume_goal
from route_matching import group_routes
import sprint_baseline as sbase
import sprint_projection as sproj
import sprint_plan_generator as spgen
import sprint_tracking as strack
import warmup_cooldown as wcd
import run_chat as rchat
from intervals import analyze_intervals, analyze_intervals_timed
from laps import detect_laps
from insights import generate_run_insight
from metrics import get_metrics_trend, compute_interval_metrics, compute_lap_metrics
import asyncio as _asyncio
import garmin_transform as gt
from garmin_client import GarminClient
import fitness_model as fmodel
import fitness_projection as fproj
import plan_generator as pgen
import plan_adherence as padh
import pace_adaptation as padapt
import readiness as rdns
import heat
import weather
import coach_llm

garmin = GarminClient()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

strava = StravaClient()

# In-memory progress tracking for long-running imports
# key: job_id (str), value: dict with status, imported, total, etc.
import_progress: dict[str, dict[str, Any]] = {}


# Everything downstream — calibration, adherence, readiness — is only as fresh as
# the last import. Leaving that to a button meant a run could land on the watch and
# never reach the plan.
AUTO_SYNC_INTERVAL_SEC = 2 * 60 * 60
last_auto_sync: dict[str, Any] = {"at": None, "imported": 0, "error": None}


async def _auto_sync_loop() -> None:
    while True:
        try:
            async with async_session() as session:
                res = await import_garmin_sync(session)
            # Today's recovery numbers keep settling through the morning, so refresh
            # them on the same beat rather than pinning whatever we saw first.
            async with async_session() as session:
                await _wellness(session, datetime.utcnow().date().isoformat(), refresh=True)
            last_auto_sync.update({"at": datetime.utcnow().isoformat(),
                                   "imported": res.get("imported", 0), "error": None})
            if res.get("imported"):
                logger.info("Auto-sync imported %s new run(s)", res["imported"])
        except Exception as exc:  # noqa: BLE001 — a failed sync must never kill the loop
            logger.warning("Auto-sync failed: %s", exc)
            last_auto_sync.update({"at": datetime.utcnow().isoformat(), "error": str(exc)[:200]})
        await _asyncio.sleep(AUTO_SYNC_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = _asyncio.create_task(_auto_sync_loop())
    yield
    task.cancel()
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
        "is_interval": act.is_interval,
        "interval_config": act.interval_config,
        "source": act.source,
        "average_heartrate": act.average_heartrate,
        "max_heartrate": act.max_heartrate,
        "average_cadence": act.average_cadence,
        "hr_zones": act.hr_zones,
        "running_dynamics": act.running_dynamics,
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
        "average_cadence": s.average_cadence,
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
    latlng_data = None
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
                if stream_type == "latlng":
                    latlng_data = stream_obj.get("data")

    # Generate summary polyline from latlng stream if not already set
    if latlng_data and not act.map_summary_polyline:
        act.map_summary_polyline = encode_polyline(latlng_data)

    act.has_detailed_data = True

    # Auto-compute best efforts from streams
    try:
        await compute_and_store_best_efforts(session, act.id)
    except Exception as exc:
        logger.warning("Failed to compute best efforts for activity %s: %s", act.id, exc)


async def _persist_garmin_activity(session, summary, splits_payload, details, zones) -> None:
    """Map Garmin payloads into Activity/Split/Stream and run the analysis helpers."""
    fields = gt.summary_to_activity_fields(summary)
    activity_id = fields["id"]

    act = await session.get(Activity, activity_id)
    if act is None:
        act = Activity(id=activity_id)
        session.add(act)
    for k, v in fields.items():
        if k != "id":
            setattr(act, k, v)

    # Splits (replace existing)
    for old in (await session.execute(select(Split).where(Split.activity_id == activity_id))).scalars().all():
        await session.delete(old)
    for sd in gt.laps_to_splits(splits_payload):
        session.add(Split(activity_id=activity_id, **sd))

    # Streams (replace existing)
    for old in (await session.execute(select(Stream).where(Stream.activity_id == activity_id))).scalars().all():
        await session.delete(old)
    streams = gt.details_to_streams(details)
    for stype, data in streams.items():
        session.add(Stream(activity_id=activity_id, stream_type=stype, data=data))

    if streams.get("latlng"):
        act.map_summary_polyline = encode_polyline(streams["latlng"])
    act.hr_zones = gt.hr_zones(zones)
    act.running_dynamics = gt.running_dynamics_summary(streams)
    act.has_detailed_data = True

    try:
        await compute_and_store_best_efforts(session, activity_id)
    except Exception as exc:
        logger.warning("best efforts failed for %s: %s", activity_id, exc)


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


@app.get("/api/training")
async def training(session: AsyncSession = Depends(get_session)):
    """Adaptive-coaching model: fitness metrics, gray-zone classification, warnings."""
    result = await session.execute(select(Activity))
    acts = [
        {
            "id": a.id, "name": a.name, "distance": a.distance,
            "start_date": a.start_date, "average_speed": a.average_speed,
            "average_heartrate": a.average_heartrate, "max_heartrate": a.max_heartrate,
        }
        for a in result.scalars().all()
    ]
    return fmodel.training_report(acts, datetime.utcnow())


# ---------------------------------------------------------------------------
# Plan builder (v2a)
# ---------------------------------------------------------------------------

class PlanCreateRequest(BaseModel):
    weeks: int
    goal_type: str = "5k"                       # "5k" | "sprint_100m"
    target_time_sec: Optional[int] = None       # 5K target (whole sec)
    target_100m_sec: Optional[float] = None      # sprint target (sub-second); None -> horizon projection


async def _plan_activity_dicts(session: AsyncSession) -> list[dict[str, Any]]:
    acts = (await session.execute(select(Activity))).scalars().all()
    return [
        {"id": a.id, "name": a.name, "distance": a.distance, "start_date": a.start_date,
         "average_speed": a.average_speed, "average_heartrate": a.average_heartrate,
         "max_heartrate": a.max_heartrate,
         # Heat-normalised pace — what every cross-season comparison should use.
         "normalized_pace_sec": a.normalized_pace_sec,
         "temp_c": a.temp_c, "dew_point_c": a.dew_point_c,
         "heat_penalty_sec": a.heat_penalty_sec}
        for a in acts
    ]


def _fmt_time(sec: Optional[int]) -> str:
    if not sec:
        return "?"
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _weeks_overview(workouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_week: dict[int, dict[str, Any]] = {}
    for w in workouts:
        wk = by_week.setdefault(w["week_number"], {"week": w["week_number"], "sessions": 0, "long_km": 0})
        wk["sessions"] += 1
        if w["day_type"] == "long" and w.get("target_distance_m"):
            wk["long_km"] = round(w["target_distance_m"] / 1000, 1)
    return [by_week[k] for k in sorted(by_week)]


async def _workout_dicts(session: AsyncSession, plan: Plan) -> list[dict[str, Any]]:
    wos = (await session.execute(
        select(PlannedWorkout).where(PlannedWorkout.plan_id == plan.id).order_by(PlannedWorkout.date)
    )).scalars().all()
    return [
        {"id": w.id, "date": w.date, "week_number": w.week_number, "day_type": w.day_type,
         "target_distance_m": w.target_distance_m, "pace_low_sec": w.pace_low_sec,
         "pace_high_sec": w.pace_high_sec, "hr_ceiling": w.hr_ceiling,
         "title": w.title, "description": w.description, "structure": w.structure,
         "garmin_workout_id": w.garmin_workout_id}
        for w in wos
    ]


def _serialize_workouts(workouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for w in workouts:
        d = dict(w)
        d["date"] = w["date"].isoformat() if w.get("date") else None
        out.append(d)
    return out


async def _plan_response(session: AsyncSession, plan: Plan) -> dict[str, Any]:
    workout_dicts = await _workout_dicts(session, plan)
    now = datetime.utcnow()
    plan_out = {
        "id": plan.id, "goal_type": plan.goal_type, "target_time_sec": plan.target_time_sec,
        "sprint_target_sec": plan.sprint_target_sec,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "goal_date": plan.goal_date.isoformat() if plan.goal_date else None,
        "weeks": plan.weeks, "status": plan.status, "narrative": plan.narrative,
    }

    if plan.goal_type == "sprint_100m":
        plan_out["profile"] = plan.fitness_snapshot
        interval_acts = await _sprint_interval_activities(session)
        tracked = strack.match_sprint_sessions(workout_dicts, interval_acts, now, plan.start_date)
        return {
            "plan": plan_out,
            "workouts": _serialize_workouts(tracked["workouts"]),
            "progress": tracked["progress"],
        }

    acts = await _plan_activity_dicts(session)
    graded = padh.match_and_grade(workout_dicts, acts, now, plan.start_date)
    return {
        "plan": plan_out,
        "workouts": _serialize_workouts(graded["workouts"]),
        "adherence": graded["summary"],
    }


def _fmt_sprint(sec: Optional[float]) -> str:
    return f"{sec:.1f}s" if sec else "?"


async def _sprint_baseline_inputs(session: AsyncSession):
    be_rows = (await session.execute(
        select(BestEffort.distance_target, BestEffort.time_seconds, Activity.start_date)
        .join(Activity, Activity.id == BestEffort.activity_id)
        .where(BestEffort.distance_target.in_([100, 200]))
    )).all()
    best_efforts = [
        {"distance_target": d, "time_seconds": t, "start_date": sd}
        for d, t, sd in be_rows
    ]
    iv_rows = (await session.execute(
        select(Activity.start_date, Activity.interval_config).where(Activity.is_interval.is_(True))
    )).all()
    interval_configs = [{"start_date": sd, "config": cfg} for sd, cfg in iv_rows if cfg]
    return best_efforts, interval_configs


async def _sprint_profile(session: AsyncSession, now: datetime) -> dict[str, Any]:
    best_efforts, interval_configs = await _sprint_baseline_inputs(session)
    return sbase.build_sprint_profile(best_efforts, interval_configs, now)


async def _sprint_interval_activities(session: AsyncSession) -> list[dict[str, Any]]:
    """Interval-tagged activities with their best 100m + fade/fastest-rep, for tracking."""
    rows = (await session.execute(
        select(Activity.id, Activity.start_date, Activity.interval_config)
        .where(Activity.is_interval.is_(True))
    )).all()
    be100 = dict((aid, t) for aid, t in (await session.execute(
        select(BestEffort.activity_id, func.min(BestEffort.time_seconds))
        .where(BestEffort.distance_target == 100).group_by(BestEffort.activity_id)
    )).all())
    out = []
    for aid, sd, cfg in rows:
        fade = fastest = None
        if isinstance(cfg, dict):
            result = cfg.get("result") or {}
            summ = result.get("summary") or {}
            fpace, space = summ.get("fastest_rep_pace"), summ.get("slowest_rep_pace")
            if fpace and space:
                fade = round((space / fpace - 1) * 100, 1)
            reps = [s.get("duration_s") for s in (result.get("segments") or [])
                    if s.get("type") == "rep" and s.get("duration_s")]
            fastest = min(reps) if reps else None
        out.append({"id": aid, "start_date": sd, "best_100m_sec": be100.get(aid),
                    "fade_pct": fade, "fastest_rep_sec": fastest})
    return out


def _sprint_weeks_overview(workouts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_week: dict[int, list[dict[str, Any]]] = {}
    for w in workouts:
        by_week.setdefault(w["week_number"], []).append(w)
    max_wk = max(by_week) if by_week else 0
    out = []
    for wk in sorted(by_week):
        types = [x["day_type"] for x in by_week[wk]]
        sessions = [t for t in types if t != "rest"]
        if wk == max_wk:
            phase = "taper"
        elif wk == 1 and "test" in types:
            phase = "foundation"
        elif "speed_endurance" not in types and len(sessions) <= 2:
            phase = "deload"
        elif wk <= 2:
            phase = "foundation"
        else:
            phase = "development"
        out.append({"week": wk, "phase": phase, "focus": ", ".join(sorted(set(sessions)))})
    return out


@app.get("/api/plan/sprint/baseline")
async def sprint_baseline_endpoint(session: AsyncSession = Depends(get_session)):
    """Data-derived 100m sprint profile (best efforts + interval history)."""
    return await _sprint_profile(session, datetime.utcnow())


@app.get("/api/plan/sprint/projections")
async def sprint_projections_endpoint(session: AsyncSession = Depends(get_session)):
    """Sprint profile + realistic 100m targets at fixed horizons."""
    now = datetime.utcnow()
    profile = await _sprint_profile(session, now)
    current = profile.get("best_100m_sec") or 20.0
    proj = sproj.sprint_projections(current, now)
    return {"profile": profile, **proj}


@app.get("/api/plan/projections")
async def plan_projections(session: AsyncSession = Depends(get_session)):
    """Current 5K estimate + realistic targets at fixed horizons."""
    dicts = await _plan_activity_dicts(session)
    return fproj.projections(dicts, datetime.utcnow())


async def _abandon_active_plans(session: AsyncSession) -> None:
    for old in (await session.execute(select(Plan).where(Plan.status == "active"))).scalars().all():
        old.status = "abandoned"


async def _create_sprint_plan(req: PlanCreateRequest, session: AsyncSession) -> dict[str, Any]:
    now = datetime.utcnow()
    profile = await _sprint_profile(session, now)
    current = profile.get("best_100m_sec") or 20.0
    target = req.target_100m_sec
    if target is None:
        target = sproj.sprint_projections(current, now, horizons=(req.weeks,))["horizons"][0]["target_100m_sec"]
    gen = spgen.generate_sprint_plan(profile, req.weeks, target, now)

    await _abandon_active_plans(session)
    plan = Plan(
        goal_type="sprint_100m", goal_distance_m=100.0,
        target_time_sec=round(target), sprint_target_sec=target,
        start_date=now, goal_date=gen["goal_date"], weeks=req.weeks, status="active",
        created_at=now, fitness_snapshot=profile,
    )
    session.add(plan)
    await session.flush()
    for w in gen["workouts"]:
        session.add(PlannedWorkout(plan_id=plan.id, **w))

    plan.narrative = await coach_llm.generate_sprint_narrative(
        {"target_str": _fmt_sprint(target), "current_str": _fmt_sprint(current), "weeks": req.weeks},
        profile, _sprint_weeks_overview(gen["workouts"]),
    )
    await session.commit()
    return await _plan_response(session, plan)


@app.post("/api/plan")
async def create_plan(req: PlanCreateRequest, session: AsyncSession = Depends(get_session)):
    if not (4 <= req.weeks <= 20):
        raise HTTPException(status_code=400, detail="weeks must be between 4 and 20")
    if req.goal_type == "sprint_100m":
        return await _create_sprint_plan(req, session)
    if req.target_time_sec is None:
        raise HTTPException(status_code=400, detail="target_time_sec required for 5k plans")
    now = datetime.utcnow()
    dicts = await _plan_activity_dicts(session)

    model = fmodel.build_fitness_model(dicts, now)
    gen = pgen.generate_plan(model, req.weeks, req.target_time_sec, now)

    # Only one active plan at a time.
    for old in (await session.execute(select(Plan).where(Plan.status == "active"))).scalars().all():
        old.status = "abandoned"

    plan = Plan(
        goal_type="5k", goal_distance_m=5000.0, target_time_sec=req.target_time_sec,
        start_date=now, goal_date=gen["goal_date"], weeks=req.weeks, status="active",
        created_at=now, fitness_snapshot=model,
    )
    session.add(plan)
    await session.flush()  # assign plan.id
    for w in gen["workouts"]:
        session.add(PlannedWorkout(plan_id=plan.id, **w))

    plan.narrative = await coach_llm.generate_plan_narrative(
        {"target_str": _fmt_time(req.target_time_sec), "weeks": req.weeks},
        _weeks_overview(gen["workouts"]),
    )
    await session.commit()
    return await _plan_response(session, plan)


@app.get("/api/plan")
async def get_active_plan(session: AsyncSession = Depends(get_session)):
    plan = (await session.execute(
        select(Plan).where(Plan.status == "active").order_by(Plan.id.desc())
    )).scalars().first()
    if plan is None:
        return {"plan": None, "workouts": []}
    return await _plan_response(session, plan)


@app.delete("/api/plan/{plan_id}")
async def abandon_plan(plan_id: int, session: AsyncSession = Depends(get_session)):
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Pull this plan's workouts off the Garmin watch so we don't strand them there.
    wos = (await session.execute(
        select(PlannedWorkout).where(
            PlannedWorkout.plan_id == plan.id,
            PlannedWorkout.garmin_workout_id.isnot(None),
        )
    )).scalars().all()
    removed = 0
    for w in wos:
        try:
            await garmin.remove_workout(w.garmin_workout_id)
            removed += 1
        except Exception as exc:  # noqa: BLE001 — a Garmin hiccup must not block abandoning
            logger.warning("Could not remove Garmin workout %s: %s", w.garmin_workout_id, exc)
        w.garmin_workout_id = None

    plan.status = "abandoned"
    await session.commit()
    return {"status": "abandoned", "id": plan_id, "removed_from_watch": removed}


class SuggestionApplyRequest(BaseModel):
    id: str


class WorkoutMoveRequest(BaseModel):
    date: str  # YYYY-MM-DD


def _downsample(seq: Optional[list], target: int = 120) -> Optional[list]:
    if not seq:
        return seq
    if len(seq) <= target:
        return seq
    step = len(seq) / target
    return [seq[int(i * step)] for i in range(target)]


def _workout_verdict(w: PlannedWorkout, ew: Optional[dict[str, Any]],
                     actual: Optional[dict[str, Any]]) -> Optional[str]:
    if ew is None:
        return None
    status = ew.get("status")
    if status == "upcoming":
        return "Coming up — run it as prescribed and it'll log here afterwards."
    if status == "missed":
        return "No run logged near this day. Skip it and move on — don't cram it in."
    comp = ew.get("compliance")
    avg = actual.get("avg_hr") if actual else None
    ceil = w.hr_ceiling
    if comp == "ran_hard" and avg and ceil:
        over = int(round(avg - ceil))
        return (f"You ran this easy day at {int(round(avg))} bpm — about {over} over the "
                f"{ceil} bpm ceiling. Easy days are supposed to feel genuinely easy; that's what "
                "builds the aerobic base you're chasing. Slow right down next time and let the HR settle.")
    base = None
    if comp == "on_target" and avg and ceil:
        base = (f"Nicely done — you held {int(round(avg))} bpm, under the {ceil} bpm ceiling. "
                "That's real easy running, and it's exactly what pays off on race day.")
    else:
        base = "Logged and matched to this workout. Keep it rolling."
    phases = actual.get("phases") if actual else None
    if phases:
        if phases.get("has_warmup") and phases.get("has_cooldown"):
            base += (f" You eased in for ~{phases['warmup_sec'] // 60} min and cooled down for "
                     f"~{phases['cooldown_sec'] // 60} min — good habits that stick.")
        elif phases.get("has_warmup"):
            base += f" Nice {phases['warmup_sec'] // 60}-min warm-up; try tacking on a short cool-down jog too."
        elif phases.get("has_cooldown"):
            base += " Good cool-down; ease into the next one with a couple of easy minutes first."
        elif phases.get("main_pace_sec"):
            base += " Looks like you ran it at an even pace — no distinct warm-up/cool-down. Book-end it with 2 easy min next time."
    return base


def _sprint_workout_verdict(ew: Optional[dict[str, Any]], actual: Optional[dict[str, Any]],
                            target_sec: Optional[float]) -> Optional[str]:
    if ew is None:
        return None
    status = ew.get("status")
    if status == "upcoming":
        return "Coming up — hit the reps at full quality with full recovery. It'll log here once you run it."
    if status == "missed":
        return "No sprint session logged near this day. Let it go — freshness matters more than making it up."
    best = actual.get("best_100m_sec") if actual else None
    fade = actual.get("fade_pct") if actual else None
    bits = ["Logged and matched to this session."]
    if best:
        bits.append(f"Best 100m in it: {best:.1f}s" + (f" (target {target_sec:.1f}s)." if target_sec else "."))
    if fade is not None:
        bits.append(f"Fade across reps: {fade:.0f}% — the lower this trends over the plan, the faster your 100m gets.")
    return " ".join(bits)


async def _sprint_workout_detail(w: PlannedWorkout, plan: Plan,
                                 session: AsyncSession) -> dict[str, Any]:
    workout_dicts = await _workout_dicts(session, plan)
    interval_acts = await _sprint_interval_activities(session)
    tracked = strack.match_sprint_sessions(
        workout_dicts, interval_acts, datetime.utcnow(), plan.start_date if plan else None
    )
    ew = next((x for x in tracked["workouts"] if x["id"] == w.id), None)

    actual = None
    if ew and ew.get("actual"):
        a = ew["actual"]
        act = await session.get(Activity, a["activity_id"])
        reps = []
        if act and isinstance(act.interval_config, dict):
            for s in (act.interval_config.get("result") or {}).get("segments", []):
                if s.get("type") == "rep":
                    reps.append({"rep": s.get("rep_number"), "distance_m": s.get("distance_m"),
                                 "duration_s": s.get("duration_s"), "pace_sec_per_km": s.get("pace_sec_per_km")})
        actual = {
            "activity_id": a["activity_id"],
            "name": act.name if act else None,
            "date": act.start_date.date().isoformat() if act and act.start_date else None,
            "best_100m_sec": a.get("best_100m_sec"),
            "fade_pct": a.get("fade_pct"),
            "fastest_rep_sec": a.get("fastest_rep_sec"),
            "reps": reps,
        }

    return {
        "workout": {
            "id": w.id, "date": w.date.isoformat() if w.date else None,
            "week_number": w.week_number, "day_type": w.day_type,
            "title": w.title, "description": w.description, "structure": w.structure,
            "garmin_workout_id": w.garmin_workout_id,
            "status": ew.get("status") if ew else None,
        },
        "plan": {"goal_type": "sprint_100m", "sprint_target_sec": plan.sprint_target_sec,
                 "weeks": plan.weeks, "goal_date": plan.goal_date.isoformat() if plan.goal_date else None},
        "actual": actual,
        "verdict": _sprint_workout_verdict(ew, actual, plan.sprint_target_sec),
    }


@app.get("/api/plan/workout/{workout_id}")
async def plan_workout_detail(workout_id: int, session: AsyncSession = Depends(get_session)):
    """Plan-aware detail for one workout: planned targets, the matched run's HR
    breakdown, how it fit the plan, and a plain-language verdict."""
    w = await session.get(PlannedWorkout, workout_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    plan = await session.get(Plan, w.plan_id)
    if plan and plan.goal_type == "sprint_100m":
        return await _sprint_workout_detail(w, plan, session)

    workout_dicts = await _workout_dicts(session, plan)
    acts = await _plan_activity_dicts(session)
    graded = padh.match_and_grade(
        workout_dicts, acts, datetime.utcnow(), plan.start_date if plan else None
    )
    ew = next((x for x in graded["workouts"] if x["id"] == workout_id), None)

    actual = None
    if ew and ew.get("actual"):
        aid = ew["actual"]["activity_id"]
        act = await session.get(Activity, aid)
        streams = {s.stream_type: s.data for s in (await session.execute(
            select(Stream).where(
                Stream.activity_id == aid,
                Stream.stream_type.in_(["heartrate", "distance", "time"]),
            )
        )).scalars().all()}
        actual = {
            "activity_id": aid,
            "name": act.name if act else None,
            "distance_m": act.distance if act else None,
            "avg_hr": act.average_heartrate if act else None,
            "max_hr": act.max_heartrate if act else None,
            "pace_sec": ew["actual"].get("pace_sec"),
            "hr_zones": act.hr_zones if act else None,
            "heartrate": _downsample(streams.get("heartrate")),
            "phases": wcd.detect_warmup_cooldown(streams.get("distance"), streams.get("time")),
        }

    return {
        "workout": {
            "id": w.id, "date": w.date.isoformat() if w.date else None,
            "week_number": w.week_number, "day_type": w.day_type,
            "target_distance_m": w.target_distance_m, "pace_low_sec": w.pace_low_sec,
            "pace_high_sec": w.pace_high_sec, "hr_ceiling": w.hr_ceiling,
            "title": w.title, "description": w.description, "structure": w.structure,
            "garmin_workout_id": w.garmin_workout_id,
            "status": ew.get("status") if ew else None,
            "compliance": ew.get("compliance") if ew else None,
        },
        "plan": ({"target_time_sec": plan.target_time_sec, "weeks": plan.weeks,
                  "goal_date": plan.goal_date.isoformat() if plan.goal_date else None}
                 if plan else None),
        "actual": actual,
        "verdict": _workout_verdict(w, ew, actual),
    }


HARD_DAY_TYPES = {"quality", "long"}


@app.patch("/api/plan/workout/{workout_id}")
async def move_workout(workout_id: int, req: WorkoutMoveRequest,
                       session: AsyncSession = Depends(get_session)):
    """Move a single planned workout to a new date (per-workout override).

    Warns (does not block) if the move stacks two hard sessions back-to-back.
    Re-derives the workout's week_number so the calendar regroups it.
    """
    w = await session.get(PlannedWorkout, workout_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    try:
        new_date = datetime.strptime(req.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date — use YYYY-MM-DD")

    plan = await session.get(Plan, w.plan_id)

    warning = None
    if w.day_type in HARD_DAY_TYPES:
        others = (await session.execute(
            select(PlannedWorkout).where(
                PlannedWorkout.plan_id == w.plan_id, PlannedWorkout.id != workout_id
            )
        )).scalars().all()
        for o in others:
            if o.day_type in HARD_DAY_TYPES and o.date and \
                    abs((o.date.date() - new_date.date()).days) <= 1:
                warning = (f"Heads up — this puts two hard sessions within a day of each "
                           f"other (\"{o.title}\" on {o.date.date().isoformat()}).")
                break

    w.date = new_date
    # regroup into the right plan week
    if plan and plan.start_date:
        w0_mon = plan.start_date - timedelta(days=plan.start_date.weekday())
        nd_mon = new_date - timedelta(days=new_date.weekday())
        wk = int((nd_mon - w0_mon).days / 7) + 1
        w.week_number = max(1, min(plan.weeks, wk))

    # If it's already on the watch, keep Garmin in step with the move.
    synced = None
    if w.garmin_workout_id:
        try:
            await _garmin_push(w)
            synced = True
        except Exception as exc:  # noqa: BLE001 — a Garmin hiccup must not block the move
            logger.warning("Garmin re-sync after move failed for %s: %s", workout_id, exc)
            synced = False

    await session.commit()
    resp = await _plan_response(session, plan)
    resp["warning"] = warning
    resp["garmin_synced"] = synced
    return resp


async def _active_plan(session: AsyncSession) -> Optional[Plan]:
    return (await session.execute(
        select(Plan).where(Plan.status == "active").order_by(Plan.id.desc())
    )).scalars().first()


async def _plan_suggestions(session: AsyncSession, plan: Plan) -> list[dict[str, Any]]:
    workout_dicts = await _workout_dicts(session, plan)
    acts = await _plan_activity_dicts(session)
    graded = padh.match_and_grade(workout_dicts, acts, datetime.utcnow(), plan.start_date)
    return padh.suggest(graded["workouts"], graded["summary"], datetime.utcnow())


@app.get("/api/plan/suggestions")
async def get_plan_suggestions(session: AsyncSession = Depends(get_session)):
    plan = await _active_plan(session)
    if plan is None:
        return {"suggestions": []}
    return {"suggestions": await _plan_suggestions(session, plan)}


@app.post("/api/plan/suggestions/apply")
async def apply_plan_suggestion(req: SuggestionApplyRequest, session: AsyncSession = Depends(get_session)):
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")
    # Re-derive suggestions server-side and apply the matching one (authoritative).
    suggestion = next((s for s in await _plan_suggestions(session, plan) if s["id"] == req.id), None)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion no longer applies")
    for ch in suggestion.get("changes", []):
        w = await session.get(PlannedWorkout, ch["workout_id"])
        if w is not None and ch.get("value") is not None:
            setattr(w, ch["field"], ch["value"])
    await session.commit()
    return await _plan_response(session, plan)


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
            "start_index": be.start_index,
            "end_index": be.end_index,
            "is_dedicated": be.is_dedicated,
        }
        for be in sorted(act.best_efforts, key=lambda x: x.distance_target)
    ]

    # Check GPS quality
    gps_glitch_count = 0
    dist_s = next((s.data for s in act.streams if s.stream_type == "distance"), None)
    time_s = next((s.data for s in act.streams if s.stream_type == "time"), None)
    if dist_s and time_s:
        for k in range(len(dist_s) - 1):
            d = dist_s[k + 1] - dist_s[k]
            t = time_s[k + 1] - time_s[k]
            if t > 0 and d / t > 12:
                gps_glitch_count += 1
    data["gps_glitch_count"] = gps_glitch_count

    # Look up route name: check all labels against this activity's coords
    data["route_name"] = None
    if act.start_latlng and act.distance:
        label_result = await session.execute(select(RouteLabel))
        for label in label_result.scalars().all():
            # Parse route_key: "lat_lng_dist"
            parts = label.route_key.split("_")
            if len(parts) >= 3:
                try:
                    key_lat = float(parts[0])
                    key_lng = float(parts[1])
                    key_dist = float(parts[2])
                    act_lat = round(act.start_latlng[0], 3)
                    act_lng = round(act.start_latlng[1], 3)
                    act_dist = round(act.distance / 100) * 100
                    if (abs(act_lat - key_lat) < 0.005
                            and abs(act_lng - key_lng) < 0.005
                            and abs(act_dist - key_dist) / max(key_dist, 1) < 0.2):
                        data["route_name"] = label.name
                        break
                except (ValueError, IndexError):
                    continue

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

    # Sprint-only bests (all-time)
    all_time_sprint = {}
    for target in TARGET_DISTANCES:
        result = await session.execute(
            select(BestEffort)
            .where(BestEffort.distance_target == target, BestEffort.is_dedicated.is_(True))
            .order_by(BestEffort.time_seconds.asc())
            .limit(1)
        )
        best = result.scalar_one_or_none()
        if best:
            act = await session.get(Activity, best.activity_id)
            all_time_sprint[target] = {
                "time_seconds": best.time_seconds,
                "pace_sec_per_km": best.pace_sec_per_km,
                "activity_id": best.activity_id,
                "date": act.start_date.isoformat() if act and act.start_date else None,
            }

    return {
        "all_time": {str(k): v for k, v in all_time.items()},
        "all_time_sprint": {str(k): v for k, v in all_time_sprint.items()},
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


@app.get("/api/activities/{activity_id}/intervals")
async def get_intervals(
    activity_id: int,
    reps: int = Query(..., ge=1, le=20),
    distance: int = Query(..., ge=50, le=5000),
    warmup: int = Query(0, ge=0, le=600),
    rest: int = Query(0, ge=0, le=600),
    session: AsyncSession = Depends(get_session),
):
    """Analyze intervals. If warmup/rest provided, use timed slicing. Otherwise find fastest windows."""
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
        raise HTTPException(status_code=404, detail="No GPS streams for this activity")

    if warmup > 0 or rest > 0:
        intervals = analyze_intervals_timed(
            distance_stream, time_stream, reps, distance,
            warmup_s=warmup, rest_s=rest,
        )
    else:
        intervals = analyze_intervals(distance_stream, time_stream, reps, distance)

    if not intervals:
        return {"is_interval": False, "message": "Could not analyze intervals for this run"}

    return intervals


@app.get("/api/activities/{activity_id}/interval-insights")
async def get_interval_insights(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Generate insights for an interval run: rep analysis, vs previous sessions, vs phase."""
    def _fp(s):
        """Format seconds/km as M:SS/km."""
        m, sec = divmod(int(s), 60)
        return f"{m}:{sec:02d}"

    act = await session.get(Activity, activity_id)
    if not act or not act.is_interval or not act.interval_config:
        return {"narratives": [], "tips": []}

    config = act.interval_config
    result = config.get("result")
    if not result or not result.get("is_interval"):
        return {"narratives": [], "tips": []}

    reps = [s for s in result.get("segments", []) if s.get("type") == "rep"]
    if not reps:
        return {"narratives": [], "tips": []}

    rep_dist = config.get("distance", 0)
    narratives = []
    tips = []

    # --- Within-run analysis ---
    paces = [r["pace_sec_per_km"] for r in reps if r.get("pace_sec_per_km")]
    if len(paces) >= 2:
        spread = max(paces) - min(paces)
        first_half = paces[:len(paces)//2]
        second_half = paces[len(paces)//2:]
        first_avg = sum(first_half) / len(first_half)
        second_avg = sum(second_half) / len(second_half)
        fade = second_avg - first_avg

        if spread <= 10:
            narratives.append(f"Very consistent reps — only {round(spread)}s spread across {len(paces)} reps.")
        elif spread <= 25:
            narratives.append(f"Good consistency — {round(spread)}s spread across {len(paces)} reps.")
        else:
            narratives.append(f"Wide pace variation — {round(spread)}s between fastest and slowest rep.")

        if fade > 10:
            narratives.append(f"You faded {round(fade)}s — first half averaged {_fp(first_avg)}/km vs {_fp(second_avg)}/km in the second half.")
            tips.append(f"Try starting reps a bit slower. Target {_fp(min(paces) + spread * 0.3)}/km to stay even.")
        elif fade < -10:
            narratives.append(f"Strong negative split — you got {round(abs(fade))}s faster in the second half.")
            tips.append("Great execution — building pace through reps is excellent for training adaptation.")
        else:
            narratives.append("Even pacing across all reps — well-executed session.")

    # --- vs Previous interval sessions ---
    prev_result = await session.execute(
        select(Activity)
        .where(
            Activity.id != activity_id,
            Activity.is_interval.is_(True),
            Activity.interval_config.isnot(None),
        )
        .order_by(Activity.start_date.desc())
        .limit(5)
    )
    prev_intervals = prev_result.scalars().all()

    # Find sessions with similar rep distance
    similar_sessions = []
    for prev in prev_intervals:
        if not prev.interval_config:
            continue
        prev_dist = prev.interval_config.get("distance", 0)
        if prev_dist and abs(prev_dist - rep_dist) / max(rep_dist, 1) < 0.3:
            prev_res = prev.interval_config.get("result", {})
            prev_reps = [s for s in prev_res.get("segments", []) if s.get("type") == "rep"]
            if prev_reps:
                prev_paces = [r["pace_sec_per_km"] for r in prev_reps if r.get("pace_sec_per_km")]
                if prev_paces:
                    similar_sessions.append({
                        "date": prev.start_date,
                        "avg_pace": sum(prev_paces) / len(prev_paces),
                        "reps": len(prev_reps),
                        "best_pace": min(prev_paces),
                    })

    if similar_sessions and paces:
        last = similar_sessions[0]
        this_avg = sum(paces) / len(paces)
        diff = this_avg - last["avg_pace"]
        date_str = last["date"].strftime("%b %d") if last["date"] else "previous"

        if diff < -5:
            narratives.append(f"Faster than last time! Your avg rep pace of {_fp(this_avg)}/km beats your {date_str} session ({_fp(last['avg_pace'])}/km) by {round(abs(diff))}s.")
        elif diff > 5:
            narratives.append(f"Slightly slower than your {date_str} session ({_fp(last['avg_pace'])}/km vs {_fp(this_avg)}/km today).")
        else:
            narratives.append(f"Matching your recent interval pace — {_fp(this_avg)}/km vs {_fp(last['avg_pace'])}/km on {date_str}.")

        if len(reps) > last["reps"]:
            narratives.append(f"More volume today — {len(reps)} reps vs {last['reps']} last time at similar pace.")

    # --- vs Phase regular pace ---
    phase_result = await session.execute(
        select(Activity)
        .where(
            Activity.id != activity_id,
            Activity.is_interval.isnot(True),
            Activity.distance > 500,
            Activity.average_speed > 0,
        )
        .order_by(Activity.start_date.desc())
        .limit(10)
    )
    phase_runs = phase_result.scalars().all()

    if phase_runs and paces:
        phase_paces = [1000 / r.average_speed for r in phase_runs if r.average_speed and r.average_speed > 0]
        if phase_paces:
            phase_avg = sum(phase_paces) / len(phase_paces)
            this_avg = sum(paces) / len(paces)
            speed_diff = phase_avg - this_avg

            if speed_diff > 30:
                narratives.append(f"Your rep pace ({_fp(this_avg)}/km) is {_fp(speed_diff)} faster than your regular running pace ({_fp(phase_avg)}/km).")
            elif speed_diff > 10:
                narratives.append(f"Solid speed work — reps are {_fp(speed_diff)} faster than your easy pace.")

    # --- Best effort connection ---
    if rep_dist and paces:
        be_result = await session.execute(
            select(BestEffort)
            .where(BestEffort.distance_target <= rep_dist)
            .order_by(BestEffort.distance_target.desc(), BestEffort.time_seconds.asc())
        )
        best_efforts = {}
        for be in be_result.scalars().all():
            if be.distance_target not in best_efforts:
                best_efforts[be.distance_target] = be.time_seconds

        # Find closest best effort distance
        closest_dist = max((d for d in best_efforts if d <= rep_dist), default=None)
        if closest_dist and closest_dist >= rep_dist * 0.8:
            be_time = best_efforts[closest_dist]
            be_pace = be_time / (closest_dist / 1000)
            this_best_rep = min(paces)
            if abs(this_best_rep - be_pace) < 30:
                label = f"{closest_dist}m" if closest_dist < 1000 else f"{closest_dist//1000}km"
                narratives.append(f"Your fastest rep ({_fp(this_best_rep)}/km) is close to your all-time best {label} pace ({_fp(be_pace)}/km).")

    return {"narratives": narratives, "tips": tips}


@app.get("/api/activities/{activity_id}/laps")
async def get_laps(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Detect laps from GPS data (loop runs)."""
    result = await session.execute(
        select(Stream).where(
            Stream.activity_id == activity_id,
            Stream.stream_type.in_(["latlng", "distance", "time"]),
        )
    )
    streams = {s.stream_type: s.data for s in result.scalars().all()}

    latlng = streams.get("latlng")
    distance = streams.get("distance")
    time = streams.get("time")

    if not latlng or not distance or not time:
        return {"lap_count": 0}

    laps = detect_laps(latlng, distance, time)
    if not laps:
        return {"lap_count": 0}

    return laps


@app.get("/api/activities/{activity_id}/insights")
async def get_insights(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Generate narrative insights for a run."""
    insight = await generate_run_insight(session, activity_id)
    if not insight:
        return {"narratives": [], "tips": []}
    return insight


@app.get("/api/routes")
async def get_routes(session: AsyncSession = Depends(get_session)):
    """Group activities by route similarity and return route stats."""
    result = await session.execute(
        select(Activity).order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    act_dicts = []
    polyline_map = {}  # activity_id -> polyline
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
            "polyline": a.map_summary_polyline,
            "is_interval": a.is_interval,
        })
        if a.map_summary_polyline:
            polyline_map[a.id] = a.map_summary_polyline

    routes = group_routes(act_dicts)

    # Apply route merges: combine routes that have been manually merged
    merge_result = await session.execute(select(RouteMerge))
    merges = merge_result.scalars().all()
    if merges:
        # Build mapping: from_key -> to_key. Multiple from_keys can map to same to_key.
        merge_map: dict[str, str] = {}
        for m in merges:
            merge_map[m.from_key] = m.to_key

        # Resolve chains: if A->B and B->C, then A->C
        def resolve(key: str) -> str:
            visited = set()
            while key in merge_map and key not in visited:
                visited.add(key)
                key = merge_map[key]
            return key

        # Group routes by their resolved key
        resolved_routes: dict[str, dict] = {}
        for route in routes:
            target_key = resolve(route["route_key"])
            if target_key not in resolved_routes:
                route["route_key"] = target_key
                resolved_routes[target_key] = route
            else:
                # Merge this route's data into the target
                target = resolved_routes[target_key]
                target["activities"].extend(route["activities"])
                target["activity_ids"].extend(route.get("activity_ids", []))
                target["run_count"] += route["run_count"]

        # Re-sort activities and recompute stats for merged routes
        for route in resolved_routes.values():
            route["activities"].sort(key=lambda a: a.get("date") or "")
            # Exclude interval runs from best pace calculation
            paces = [a["pace_sec_per_km"] for a in route["activities"] if a.get("pace_sec_per_km") and not a.get("is_interval")]
            if paces:
                route["best_pace_sec_per_km"] = round(min(paces), 1)
            times = [a["moving_time"] for a in route["activities"] if a.get("moving_time")]
            if times:
                route["best_time"] = min(times)
            distances = [a["distance"] for a in route["activities"] if a.get("distance")]
            if distances:
                route["avg_distance_km"] = round(sum(distances) / len(distances) / 1000, 2)

        routes = sorted(resolved_routes.values(), key=lambda r: r["run_count"], reverse=True)

    # Attach a polyline to each route for map preview
    for route in routes:
        polyline = None
        # Try best pace activity first, then first activity with a polyline
        for aid_key in [route.get("best_pace_id"), route.get("best_time_id")]:
            if aid_key and aid_key in polyline_map:
                polyline = polyline_map[aid_key]
                break
        if not polyline:
            for act in route.get("activities", []):
                if act["id"] in polyline_map:
                    polyline = polyline_map[act["id"]]
                    break
        route["polyline"] = polyline

    # Attach custom route labels
    label_result = await session.execute(select(RouteLabel))
    labels = {l.route_key: l.name for l in label_result.scalars().all()}
    for route in routes:
        custom_name = labels.get(route["route_key"])
        if custom_name:
            route["custom_name"] = custom_name

    return {"routes": routes, "total_routes": len(routes)}


class RouteLabelRequest(BaseModel):
    route_key: str
    name: str


@app.post("/api/routes/label")
async def label_route(req: RouteLabelRequest, session: AsyncSession = Depends(get_session)):
    """Set a custom name for a route."""
    result = await session.execute(
        select(RouteLabel).where(RouteLabel.route_key == req.route_key)
    )
    label = result.scalar_one_or_none()
    if label:
        label.name = req.name
    else:
        label = RouteLabel(route_key=req.route_key, name=req.name)
        session.add(label)
    await session.commit()
    return {"route_key": req.route_key, "name": req.name}


class RouteMergeRequest(BaseModel):
    source_route_key: str
    target_route_key: str


@app.post("/api/routes/merge")
async def merge_routes(req: RouteMergeRequest, session: AsyncSession = Depends(get_session)):
    """Merge one route into another. All activities from source will appear under target."""
    if req.source_route_key == req.target_route_key:
        raise HTTPException(status_code=400, detail="Cannot merge a route with itself")
    # Check if this merge already exists
    existing = await session.execute(
        select(RouteMerge).where(
            RouteMerge.from_key == req.source_route_key,
            RouteMerge.to_key == req.target_route_key,
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "already_merged"}
    merge = RouteMerge(from_key=req.source_route_key, to_key=req.target_route_key)
    session.add(merge)
    await session.commit()
    return {"status": "merged", "from_key": req.source_route_key, "to_key": req.target_route_key}


@app.get("/api/routes/labels")
async def get_route_labels(session: AsyncSession = Depends(get_session)):
    """Get all route labels (for use on activity detail pages)."""
    result = await session.execute(select(RouteLabel))
    labels = {l.route_key: l.name for l in result.scalars().all()}
    return {"labels": labels}


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

@app.get("/api/goals/recommend/speed/{distance}")
async def recommend_speed(distance: int, session: AsyncSession = Depends(get_session)):
    """Get speed goal recommendation for a distance."""
    if distance not in TARGET_DISTANCES:
        raise HTTPException(status_code=400, detail=f"Invalid distance. Use: {TARGET_DISTANCES}")
    return await recommend_speed_goal(session, distance)


@app.get("/api/goals/recommend/consistency")
async def recommend_consistency(session: AsyncSession = Depends(get_session)):
    """Get consistency goal recommendation."""
    return await recommend_consistency_goal(session)


@app.get("/api/goals/recommend/volume")
async def recommend_volume(session: AsyncSession = Depends(get_session)):
    """Get volume goal recommendation."""
    return await recommend_volume_goal(session)


class GoalCreateRequest(BaseModel):
    goal_type: str  # "speed", "consistency", "volume"
    distance_target: int | None = None
    time_target: float | None = None
    weekly_runs_target: int | None = None
    weekly_km_target: float | None = None
    mode: str | None = None  # "sprint" or "any" (for speed goals)


@app.post("/api/goals")
async def create_goal(req: GoalCreateRequest, session: AsyncSession = Depends(get_session)):
    """Create a new goal."""
    goal = Goal(
        goal_type=req.goal_type,
        distance_target=req.distance_target,
        time_target=req.time_target,
        weekly_runs_target=req.weekly_runs_target,
        weekly_km_target=req.weekly_km_target,
        mode=req.mode,
        created_at=datetime.now(),
        active=True,
    )
    session.add(goal)
    await session.commit()
    return {"id": goal.id, "status": "created"}


@app.get("/api/goals")
async def list_goals(session: AsyncSession = Depends(get_session)):
    """List all active goals with current progress."""
    result = await session.execute(
        select(Goal).where(Goal.active.is_(True)).order_by(Goal.created_at.desc())
    )
    goals = result.scalars().all()

    goal_list = []
    for g in goals:
        progress = {}
        if g.goal_type == "speed" and g.distance_target and g.time_target:
            # Get current best for this distance, filtered by mode
            query = select(BestEffort).where(BestEffort.distance_target == g.distance_target)
            if g.mode == "sprint":
                query = query.where(BestEffort.is_dedicated.is_(True))
            be_result = await session.execute(
                query.order_by(BestEffort.time_seconds.asc()).limit(1)
            )
            best = be_result.scalar_one_or_none()

            # Also get the other mode's best for context
            other_query = select(BestEffort).where(BestEffort.distance_target == g.distance_target)
            if g.mode == "sprint":
                other_query = other_query.where(BestEffort.is_dedicated.isnot(True))
            else:
                other_query = other_query.where(BestEffort.is_dedicated.is_(True))
            other_result = await session.execute(
                other_query.order_by(BestEffort.time_seconds.asc()).limit(1)
            )
            other_best = other_result.scalar_one_or_none()

            progress = {
                "current_best": best.time_seconds if best else None,
                "target": g.time_target,
                "gap": round(best.time_seconds - g.time_target, 1) if best else None,
                "achieved": best.time_seconds <= g.time_target if best else False,
                "other_mode_best": other_best.time_seconds if other_best else None,
                "mode": g.mode or "any",
            }
        elif g.goal_type == "consistency" and g.weekly_runs_target:
            # Count this week's runs
            now = datetime.now()
            week_start = now - timedelta(days=now.weekday())
            week_start = week_start.replace(hour=0, minute=0, second=0)
            count_result = await session.execute(
                select(func.count(Activity.id)).where(Activity.start_date >= week_start)
            )
            this_week = count_result.scalar() or 0
            progress = {
                "this_week": this_week,
                "target": g.weekly_runs_target,
                "achieved": this_week >= g.weekly_runs_target,
            }
        elif g.goal_type == "volume" and g.weekly_km_target:
            now = datetime.now()
            week_start = now - timedelta(days=now.weekday())
            week_start = week_start.replace(hour=0, minute=0, second=0)
            dist_result = await session.execute(
                select(func.sum(Activity.distance)).where(Activity.start_date >= week_start)
            )
            this_week_m = dist_result.scalar() or 0
            this_week_km = round(this_week_m / 1000, 2)
            progress = {
                "this_week_km": this_week_km,
                "target": g.weekly_km_target,
                "achieved": this_week_km >= g.weekly_km_target,
            }

        goal_list.append({
            "id": g.id,
            "goal_type": g.goal_type,
            "distance_target": g.distance_target,
            "time_target": g.time_target,
            "weekly_runs_target": g.weekly_runs_target,
            "weekly_km_target": g.weekly_km_target,
            "mode": g.mode,
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "progress": progress,
        })

    return {"goals": goal_list}


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: int, session: AsyncSession = Depends(get_session)):
    """Deactivate a goal."""
    goal = await session.get(Goal, goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal.active = False
    await session.commit()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Strava Webhook
# ---------------------------------------------------------------------------

@app.get("/api/webhook")
async def webhook_verify(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """Strava webhook subscription verification."""
    if mode == "subscribe" and token == config.STRAVA_WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return {"hub.challenge": challenge}
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/webhook")
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Strava webhook events.
    Strava sends: { aspect_type, event_time, object_id, object_type, owner_id, subscription_id }
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    object_type = body.get("object_type")
    aspect_type = body.get("aspect_type")
    activity_id = body.get("object_id")

    logger.info("Webhook received: type=%s, aspect=%s, id=%s", object_type, aspect_type, activity_id)

    # Only process activity creates/updates
    if object_type != "activity" or aspect_type not in ("create", "update"):
        return {"status": "ok"}

    if not activity_id:
        return {"status": "ok"}

    # Process in background
    background_tasks.add_task(_webhook_import_activity, activity_id)

    return {"status": "ok"}


async def _webhook_import_activity(activity_id: int):
    """Background task: fetch and import a single activity from webhook."""
    import asyncio
    # Wait a bit for Strava to finish processing streams
    await asyncio.sleep(30)
    try:
        async with async_session() as session:
            # Fetch activity from Strava API
            act_data = await strava.get_activity_detail(activity_id)

            sport = act_data.get("sport_type") or act_data.get("type") or ""
            if not _is_running(sport):
                logger.info("Webhook: skipping non-running activity %s (type=%s)", activity_id, sport)
                return

            # Upsert activity
            act = await _upsert_activity_summary(session, act_data)
            await _import_detail_and_streams(session, act)
            await session.commit()

            logger.info("Webhook: imported activity %s (%s)", activity_id, act.name)
    except Exception as exc:
        logger.error("Webhook: failed to import activity %s: %s", activity_id, exc)


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

        # Exclude interval runs from avg pace calculation
        pace_valid = [a for a in valid if not a.is_interval]
        pace_distance = sum(a.distance or 0 for a in pace_valid)
        pace_time = sum(a.moving_time or 0 for a in pace_valid)
        avg_pace_sec_per_km = None
        if pace_distance > 0 and pace_time > 0:
            avg_pace_sec_per_km = pace_time / (pace_distance / 1000)

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
                "pace_distance": 0,
                "pace_time": 0,
            }
        m = months[key]
        m["runs"] += 1
        m["total_distance"] += act.distance or 0
        m["total_time"] += act.moving_time or 0
        m["total_elevation"] += act.total_elevation_gain or 0
        # Track non-interval distance/time separately for pace calculation
        if not act.is_interval:
            m["pace_distance"] += act.distance or 0
            m["pace_time"] += act.moving_time or 0

    result_list = []
    for m in sorted(months.values(), key=lambda x: x["month"]):
        dist_km = m["total_distance"] / 1000
        # Use non-interval runs for avg pace
        pace_dist_km = m["pace_distance"] / 1000
        avg_pace = (m["pace_time"] / pace_dist_km) if pace_dist_km > 0 else None
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


@app.get("/api/stats/metrics-trend")
async def metrics_trend(
    phase_only: bool = Query(True),
    session: AsyncSession = Depends(get_session),
):
    """Consistency, fade, decay trends. phase_only=true shows current phase only."""
    result = await get_metrics_trend(session)

    if phase_only:
        # Find current phase boundary (14-day gap)
        for dataset_key in ["regular", "interval"]:
            data = result.get(dataset_key, [])
            if len(data) < 2:
                continue
            # Walk backwards to find phase start
            phase_start = len(data) - 1
            for i in range(len(data) - 1, 0, -1):
                from datetime import datetime as dt
                curr = dt.strptime(data[i]["date"], "%Y-%m-%d")
                prev = dt.strptime(data[i - 1]["date"], "%Y-%m-%d")
                if (curr - prev).days > 14:
                    break
                phase_start = i - 1
            result[dataset_key] = data[phase_start:]

    return result


@app.get("/api/stats/phase-progress")
async def phase_progress(session: AsyncSession = Depends(get_session)):
    """
    Per-session pace trend across the current phase, split by run type.
    - regular: avg_pace_sec_per_km from each non-interval run
    - interval: avg_rep_pace from each interval run's saved interval_config
    """
    from datetime import datetime as dt

    result = await session.execute(
        select(Activity)
        .where(Activity.has_detailed_data.is_(True))
        .order_by(Activity.start_date.asc())
    )
    activities = result.scalars().all()

    regular_all = []
    interval_all = []
    for act in activities:
        if not act.start_date or not act.distance or not act.moving_time:
            continue
        date_str = act.start_date.strftime("%Y-%m-%d")
        if act.is_interval and act.interval_config:
            cfg = act.interval_config or {}
            res = cfg.get("result") or {}
            summary = res.get("summary") or {}
            avg_pace = summary.get("avg_rep_pace")
            if avg_pace:
                interval_all.append({
                    "date": date_str,
                    "activity_id": act.id,
                    "pace_sec_per_km": round(avg_pace, 1),
                    "rep_distance_m": cfg.get("distance"),
                    "reps": cfg.get("reps"),
                })
        elif not act.is_interval:
            dist_km = act.distance / 1000
            if dist_km > 0:
                pace = act.moving_time / dist_km
                regular_all.append({
                    "date": date_str,
                    "activity_id": act.id,
                    "pace_sec_per_km": round(pace, 1),
                    "distance_km": round(dist_km, 2),
                })

    # Derive phase boundary from ALL activities so interval/regular share the same window
    combined_dates = sorted({d["date"] for d in regular_all} | {d["date"] for d in interval_all})
    phase_start_date = combined_dates[0] if combined_dates else None
    if len(combined_dates) >= 2:
        phase_start_date = combined_dates[-1]
        for i in range(len(combined_dates) - 1, 0, -1):
            curr = dt.strptime(combined_dates[i], "%Y-%m-%d")
            prev = dt.strptime(combined_dates[i - 1], "%Y-%m-%d")
            if (curr - prev).days > 14:
                break
            phase_start_date = combined_dates[i - 1]

    def _phase_slice(data):
        if not phase_start_date:
            return data
        return [d for d in data if d["date"] >= phase_start_date]

    regular = _phase_slice(regular_all)
    interval = _phase_slice(interval_all)

    def _summary(data):
        if not data:
            return None
        first = data[0]["pace_sec_per_km"]
        latest = data[-1]["pace_sec_per_km"]
        best = min(d["pace_sec_per_km"] for d in data)
        return {
            "first_pace": first,
            "latest_pace": latest,
            "best_pace": best,
            "delta": round(latest - first, 1),
            "session_count": len(data),
            "phase_start": data[0]["date"],
            "phase_end": data[-1]["date"],
        }

    return {
        "regular": regular,
        "interval": interval,
        "regular_summary": _summary(regular),
        "interval_summary": _summary(interval),
    }


@app.get("/api/activities/{activity_id}/metrics")
async def activity_metrics(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Get consistency/fade/decay metrics for a single activity."""
    act = await session.get(Activity, activity_id)
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")

    if act.is_interval and act.interval_config:
        metrics = compute_interval_metrics(act.interval_config)
        return metrics or {"consistency": None}

    # Regular run — need laps
    from laps import detect_laps as _detect_laps
    stream_result = await session.execute(
        select(Stream).where(
            Stream.activity_id == activity_id,
            Stream.stream_type.in_(["latlng", "distance", "time"]),
        )
    )
    streams = {s.stream_type: s.data for s in stream_result.scalars().all()}
    latlng = streams.get("latlng")
    dist = streams.get("distance")
    time = streams.get("time")

    if latlng and dist and time:
        laps = _detect_laps(latlng, dist, time)
        if laps and laps.get("lap_count", 0) >= 3:
            metrics = compute_lap_metrics(laps)
            return metrics or {"consistency": None}

    return {"consistency": None}


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


class MarkIntervalRequest(BaseModel):
    is_interval: bool
    interval_config: dict | None = None  # { reps, distance, result }


@app.post("/api/activities/{activity_id}/mark-interval")
async def mark_interval(activity_id: int, req: MarkIntervalRequest, session: AsyncSession = Depends(get_session)):
    """Mark/unmark an activity as interval and save the config."""
    act = await session.get(Activity, activity_id)
    if act is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    act.is_interval = req.is_interval
    if req.is_interval and req.interval_config:
        act.interval_config = req.interval_config
    elif not req.is_interval:
        act.interval_config = None
    await session.commit()
    return {"id": activity_id, "is_interval": act.is_interval, "interval_config": act.interval_config}


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
    already_existed = 0
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

        for act_data in activities:
            sport = act_data.get("sport_type") or act_data.get("type") or ""
            if not _is_running(sport):
                skipped += 1
                continue

            # Skip if already imported with detail
            activity_id = act_data["id"]
            existing = await session.get(Activity, activity_id)
            # Skip only if it truly has streams (not just the flag)
            if existing and existing.has_detailed_data:
                # Verify it actually has streams
                stream_check = await session.execute(
                    select(func.count(Stream.id)).where(Stream.activity_id == activity_id)
                )
                has_streams = (stream_check.scalar() or 0) > 0
                if has_streams:
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


@app.post("/api/import/garmin/sync")
async def import_garmin_sync(session: AsyncSession = Depends(get_session)):
    """Incremental Garmin sync: import running activities not already stored."""
    try:
        gc = garmin
        imported = already_existed = 0
        start, page_size = 0, 20
        while True:
            try:
                acts = await gc.get_recent_running(limit=page_size, start=start)
            except Exception as exc:
                logger.error("Garmin fetch failed at start=%d: %s", start, exc)
                if start == 0:
                    raise HTTPException(status_code=502, detail=f"Garmin fetch failed: {exc}")
                break
            if not acts:
                break
            page_all_exist = True
            for summary in acts:
                aid = summary["activityId"]
                existing = await session.get(Activity, aid)
                if existing and existing.has_detailed_data:
                    already_existed += 1
                    continue
                page_all_exist = False
                splits = await gc.get_splits(aid)
                details = await gc.get_details(aid)
                try:
                    zones = await gc.get_hr_zones(aid)
                except Exception:
                    zones = []
                await _persist_garmin_activity(session, summary, splits, details, zones)
                imported += 1
                await session.commit()
            if page_all_exist:
                break
            start += page_size
            await _asyncio.sleep(1)  # be gentle with Garmin
        return {"imported": imported, "already_existed": already_existed, "skipped_non_running": 0}
    except HTTPException:
        raise
    except RuntimeError as exc:  # missing token, etc.
        raise HTTPException(status_code=400, detail=str(exc))


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


# ---------------------------------------------------------------------------
# Chat about this run — tool-calling analyst
# ---------------------------------------------------------------------------

def _pace_from(distance_m: Optional[float], moving_time_s: Optional[int]) -> Optional[int]:
    if not distance_m or not moving_time_s or distance_m <= 0:
        return None
    return round(moving_time_s / (distance_m / 1000.0))


async def _tool_get_run(session: AsyncSession, activity_id: int) -> dict[str, Any]:
    a = await session.get(Activity, activity_id)
    if a is None:
        return {"error": "run not found"}
    splits = (await session.execute(
        select(Split).where(Split.activity_id == activity_id).order_by(Split.split_number)
    )).scalars().all()
    bes = (await session.execute(
        select(BestEffort).where(BestEffort.activity_id == activity_id)
    )).scalars().all()
    out: dict[str, Any] = {
        "id": a.id, "name": a.name,
        "date": a.start_date.date().isoformat() if a.start_date else None,
        "distance_km": round((a.distance or 0) / 1000, 2),
        "moving_time_s": a.moving_time,
        "pace_sec_per_km": _pace_from(a.distance, a.moving_time),
        "avg_hr": a.average_heartrate, "max_hr": a.max_heartrate,
        "avg_cadence": a.average_cadence, "elevation_gain_m": a.total_elevation_gain,
        "source": a.source, "is_interval": a.is_interval, "hr_zones": a.hr_zones,
        "splits": [{"km": s.split_number,
                    "pace_sec_per_km": _pace_from(s.distance, s.moving_time),
                    "avg_hr": s.average_heartrate} for s in splits][:25],
        "best_efforts": [{"distance_m": b.distance_target, "time_sec": b.time_seconds} for b in bes],
    }
    if a.is_interval and isinstance(a.interval_config, dict):
        out["interval_summary"] = (a.interval_config.get("result") or {}).get("summary")
    return out


async def _tool_find_runs(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    since = int(args.get("since_days") or 120)
    limit = min(int(args.get("limit") or 15), 25)
    cutoff = datetime.utcnow() - timedelta(days=since)
    rows = (await session.execute(
        select(Activity).where(Activity.start_date >= cutoff, Activity.distance.isnot(None))
        .order_by(Activity.start_date.desc())
    )).scalars().all()
    min_km, max_km = args.get("min_km"), args.get("max_km")
    nc = (args.get("name_contains") or "").lower()
    out = []
    for a in rows:
        km = (a.distance or 0) / 1000
        if min_km and km < min_km:
            continue
        if max_km and km > max_km:
            continue
        if nc and nc not in (a.name or "").lower():
            continue
        out.append({"id": a.id, "name": a.name,
                    "date": a.start_date.date().isoformat() if a.start_date else None,
                    "distance_km": round(km, 2),
                    "pace_sec_per_km": _pace_from(a.distance, a.moving_time),
                    "avg_hr": a.average_heartrate, "is_interval": a.is_interval})
        if len(out) >= limit:
            break
    return {"count": len(out), "runs": out}


async def _tool_best_efforts(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    dist = args.get("distance_m")
    q = select(BestEffort.distance_target, func.min(BestEffort.time_seconds)).group_by(BestEffort.distance_target)
    if dist:
        q = q.where(BestEffort.distance_target == int(dist))
    rows = (await session.execute(q)).all()
    out = []
    for d, t in rows:
        r = (await session.execute(
            select(Activity.start_date).join(BestEffort, BestEffort.activity_id == Activity.id)
            .where(BestEffort.distance_target == d, BestEffort.time_seconds == t).limit(1)
        )).first()
        out.append({"distance_m": d, "best_time_sec": t,
                    "date": r[0].date().isoformat() if r and r[0] else None})
    out.sort(key=lambda x: x["distance_m"])
    return {"best_efforts": out}


async def _tool_trend(session: AsyncSession, args: dict[str, Any]) -> dict[str, Any]:
    metric = args.get("metric") or "weekly_distance"
    weeks = min(int(args.get("weeks") or 12), 52)
    cutoff = datetime.utcnow() - timedelta(weeks=weeks)
    rows = (await session.execute(
        select(Activity).where(Activity.start_date >= cutoff, Activity.distance.isnot(None))
    )).scalars().all()
    buckets: dict[str, dict[str, float]] = {}
    for a in rows:
        iso = a.start_date.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        b = buckets.setdefault(key, {"dist": 0.0, "time": 0.0, "hr_sum": 0.0, "hr_n": 0.0})
        b["dist"] += (a.distance or 0) / 1000
        b["time"] += a.moving_time or 0
        if a.average_heartrate:
            b["hr_sum"] += a.average_heartrate
            b["hr_n"] += 1
    series = []
    for key in sorted(buckets):
        b = buckets[key]
        if metric == "weekly_distance":
            val: Optional[float] = round(b["dist"], 1)
        elif metric == "avg_pace":
            val = round(b["time"] / b["dist"]) if b["dist"] else None
        else:
            val = round(b["hr_sum"] / b["hr_n"]) if b["hr_n"] else None
        series.append({"week": key, "value": val})
    return {"metric": metric, "series": series}


async def _tool_active_plan(session: AsyncSession) -> dict[str, Any]:
    plan = (await session.execute(
        select(Plan).where(Plan.status == "active").order_by(Plan.id.desc())
    )).scalars().first()
    if plan is None:
        return {"active_plan": None}
    resp = await _plan_response(session, plan)
    p = resp["plan"]
    upcoming = [w for w in resp["workouts"] if w.get("status") == "upcoming"]
    nxt = upcoming[0] if upcoming else None
    return {
        "goal_type": p["goal_type"], "target_time_sec": p.get("target_time_sec"),
        "sprint_target_sec": p.get("sprint_target_sec"), "weeks": p["weeks"],
        "goal_date": p.get("goal_date"),
        "adherence": resp.get("adherence") or resp.get("progress"),
        "next_workout": ({"date": nxt.get("date"), "day_type": nxt.get("day_type"),
                          "title": nxt.get("title")} if nxt else None),
    }


async def _run_chat_tool(session: AsyncSession, ctx_activity_id: int,
                         name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "get_run":
        return await _tool_get_run(session, int(args.get("activity_id") or ctx_activity_id))
    if name == "find_runs":
        return await _tool_find_runs(session, args)
    if name == "get_best_efforts":
        return await _tool_best_efforts(session, args)
    if name == "get_trend":
        return await _tool_trend(session, args)
    if name == "get_active_plan":
        return await _tool_active_plan(session)
    return {"error": f"unknown tool {name}"}


class RunChatRequest(BaseModel):
    message: Optional[str] = None
    # Legacy: older clients posted the whole transcript. Accepted so a stale tab
    # doesn't break, but the server's own history is what it actually answers from.
    messages: Optional[list[dict[str, Any]]] = None


CHAT_CONTEXT_TURNS = 12


async def _chat_history(session: AsyncSession, activity_id: int) -> list[ChatMessage]:
    return list((await session.execute(
        select(ChatMessage).where(ChatMessage.activity_id == activity_id)
        .order_by(ChatMessage.id)
    )).scalars().all())


@app.get("/api/activities/{activity_id}/chat")
async def get_run_chat(activity_id: int, session: AsyncSession = Depends(get_session)):
    """The saved conversation about this run."""
    rows = await _chat_history(session, activity_id)
    return {"messages": [{"role": r.role, "content": r.content,
                          "created_at": r.created_at.isoformat() if r.created_at else None}
                         for r in rows]}


@app.delete("/api/activities/{activity_id}/chat")
async def clear_run_chat(activity_id: int, session: AsyncSession = Depends(get_session)):
    """Start the conversation over."""
    rows = await _chat_history(session, activity_id)
    for r in rows:
        await session.delete(r)
    await session.commit()
    return {"cleared": len(rows)}


@app.post("/api/activities/{activity_id}/chat")
async def run_chat_endpoint(activity_id: int, req: RunChatRequest,
                            session: AsyncSession = Depends(get_session)):
    """Grounded, tool-calling Q&A about a run and the athlete's history.

    The transcript lives server-side, so closing the tab no longer throws the
    conversation away.
    """
    act = await session.get(Activity, activity_id)
    if act is None:
        raise HTTPException(status_code=404, detail="Activity not found")

    content = (req.message or "").strip()
    if not content and req.messages:                    # legacy client
        last = [m for m in req.messages if m.get("role") == "user" and m.get("content")]
        content = str(last[-1]["content"]).strip() if last else ""
    if not content:
        raise HTTPException(status_code=400, detail="message is required")

    now = datetime.utcnow()
    session.add(ChatMessage(activity_id=activity_id, role="user",
                            content=content[:2000], created_at=now))
    await session.commit()

    history = await _chat_history(session, activity_id)
    msgs = [{"role": m.role, "content": m.content} for m in history][-CHAT_CONTEXT_TURNS:]

    async def execute_tool(tname: str, targs: dict[str, Any]) -> dict[str, Any]:
        return await _run_chat_tool(session, activity_id, tname, targs)

    result = await rchat.chat(activity_id, msgs, execute_tool, now.date().isoformat())
    reply = result["reply"]

    # Only persist a real answer — saving an error string would poison the context
    # of every later turn in this conversation.
    if result.get("ok", True) and reply:
        session.add(ChatMessage(activity_id=activity_id, role="assistant",
                                content=reply[:4000], created_at=datetime.utcnow()))
        await session.commit()

    return {"reply": reply, "ok": result.get("ok", True)}


# ---------------------------------------------------------------------------
# Push a structured workout to the Garmin watch
# ---------------------------------------------------------------------------

async def _garmin_push(w: PlannedWorkout) -> Optional[int]:
    """Upload+schedule this workout on Garmin, replacing any previous push so we
    never leave a duplicate (or a stale date) on the watch."""
    steps = (w.structure or {}).get("steps")
    if not steps:
        return None
    if w.garmin_workout_id:
        try:
            await garmin.remove_workout(w.garmin_workout_id)
        except Exception as exc:  # noqa: BLE001 — a stale id shouldn't block a re-push
            logger.warning("Could not remove old Garmin workout %s: %s", w.garmin_workout_id, exc)
        w.garmin_workout_id = None
    res = await garmin.push_workout(w.title or "RunFlow workout", steps,
                                    w.date.date().isoformat())
    w.garmin_workout_id = res["workout_id"]
    return w.garmin_workout_id


# ---------------------------------------------------------------------------
# Today's guidance — readiness + heat, applied to the session in front of you
# ---------------------------------------------------------------------------

async def _wellness(session: AsyncSession, day: str,
                    refresh: bool = False) -> dict[str, Any]:
    """Today's recovery assessment — from cache unless it's missing or forced.

    Garmin's numbers for a day keep moving until you've slept and worn the watch,
    so `refresh` re-pulls; otherwise one fetch per day is plenty.
    """
    row = await session.get(DailyWellness, day)
    if row is not None and not refresh and row.raw:
        return row.raw

    raw = await garmin.wellness(day)
    assessment = rdns.assess(
        raw.get("readiness"), raw.get("hrv"), raw.get("sleep"),
        raw.get("body_battery"), raw.get("rhr"),
    )
    if not assessment.get("available") and row is not None and row.raw:
        return row.raw  # Garmin blipped — keep what we already had rather than blanking the UI

    facts = rdns.facts(raw)
    if row is None:
        row = DailyWellness(date=day)
        session.add(row)
    row.readiness_score = assessment.get("score")
    row.readiness_level = assessment.get("garmin_level")
    row.sleep_hours = facts["sleep_hours"]
    row.sleep_score = facts["sleep_score"]
    row.body_battery_peak = facts["body_battery_peak"]
    row.hrv_last_night = facts["hrv_last_night"]
    row.hrv_status = facts["hrv_status"]
    row.resting_hr = facts["resting_hr"]
    row.raw = assessment
    row.fetched_at = datetime.utcnow()
    await session.commit()
    return assessment


async def _backfill_weather(session: AsyncSession, force: bool = False) -> dict[str, Any]:
    """Attach the conditions each run was actually run in, and a heat-normalised pace.

    Fetched in one archive request per calendar year rather than one per run.
    """
    q = select(Activity).where(Activity.average_speed.isnot(None),
                               Activity.start_date.isnot(None))
    if not force:
        q = q.where(Activity.weather_checked.is_(False) | Activity.weather_checked.is_(None))
    acts = (await session.execute(q)).scalars().all()
    if not acts:
        return {"updated": 0, "skipped": 0}

    loc = await _run_location(session)
    if not loc:
        return {"updated": 0, "skipped": len(acts), "error": "no GPS location on any run"}
    lat, lon = loc

    by_year: dict[int, list[Activity]] = {}
    for a in acts:
        by_year.setdefault(a.start_date.year, []).append(a)

    updated = skipped = 0
    for year, rows in sorted(by_year.items()):
        lo = min(a.start_date for a in rows).date().isoformat()
        hi = min(max(a.start_date for a in rows).date(),
                 (datetime.utcnow() - timedelta(days=1)).date()).isoformat()
        if lo > hi:
            skipped += len(rows)
            continue

        hourly = await weather.archive_hourly(lat, lon, lo, hi)
        offset_h = hourly.pop("_utc_offset_h", 0.0) if hourly else 0.0
        if not hourly:
            skipped += len(rows)
            continue

        for a in rows:
            local = a.start_date + timedelta(hours=offset_h)
            key = local.strftime("%Y-%m-%dT%H:00")
            cond = hourly.get(key)
            a.weather_checked = True
            if not cond:
                skipped += 1
                continue
            temp_c, dew_c = cond
            pace = 1000.0 / a.average_speed
            adj = heat.adjust(temp_c, dew_c, round(pace), round(pace))
            a.temp_c = temp_c
            a.dew_point_c = dew_c
            a.heat_index = adj["stress_index"]
            a.heat_penalty_sec = adj["penalty_sec"]
            # The pace this effort would have produced on a neutral day. THIS is
            # the number any cross-season comparison should use.
            a.normalized_pace_sec = round(pace - adj["penalty_sec"], 1)
            updated += 1
        await session.commit()

    return {"updated": updated, "skipped": skipped, "total": len(acts)}


@app.post("/api/analysis/backfill-weather")
async def backfill_weather(force: bool = False,
                           session: AsyncSession = Depends(get_session)):
    """Backfill historical conditions onto every run (idempotent)."""
    return await _backfill_weather(session, force=force)


@app.get("/api/sync/status")
async def sync_status():
    """When the data behind the plan was last refreshed."""
    return {**last_auto_sync, "interval_sec": AUTO_SYNC_INTERVAL_SEC}


@app.get("/api/analysis/pace-hr")
async def pace_hr_scatter(days: int = 365, session: AsyncSession = Depends(get_session)):
    """Every run as a (pace, heart-rate) point, against the easy zone.

    One picture of the thing three paragraphs were trying to say: whether the
    easy runs are actually easy.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    acts = (await session.execute(
        select(Activity).where(
            Activity.start_date >= cutoff,
            Activity.average_heartrate.isnot(None),
            Activity.average_speed.isnot(None),
            Activity.distance > 1000,
        ).order_by(Activity.start_date)
    )).scalars().all()

    plan = await _active_plan(session)
    snap = (plan.fitness_snapshot if plan else None) or {}
    if not snap:
        dicts = await _plan_activity_dicts(session)
        snap = fmodel.build_fitness_model(dicts, datetime.utcnow())
    ceiling = snap.get("easy_hr_ceiling")
    easy_pace = snap.get("easy_pace_sec")

    runs = []
    for a in acts:
        pace = 1000.0 / a.average_speed
        runs.append({
            "id": a.id,
            "date": a.start_date.date().isoformat(),
            "name": a.name,
            "distance_km": round(a.distance / 1000.0, 2),
            "pace_sec": round(pace),
            "avg_hr": round(a.average_heartrate),
            "in_easy_zone": bool(ceiling and a.average_heartrate <= ceiling),
        })

    in_zone = sum(1 for r in runs if r["in_easy_zone"])
    return {
        "runs": runs,
        "easy_hr_ceiling": ceiling,
        "easy_pace_sec": easy_pace,
        "max_hr": snap.get("athlete_max_hr"),
        "in_easy_zone": in_zone,
        "total": len(runs),
    }


@app.get("/api/wellness/history")
async def wellness_history(days: int = 30, session: AsyncSession = Depends(get_session)):
    """The readiness trend — what a single day's score can never show you."""
    rows = (await session.execute(
        select(DailyWellness).order_by(DailyWellness.date.desc()).limit(min(days, 90))
    )).scalars().all()
    return {"days": [{
        "date": r.date, "readiness_score": r.readiness_score,
        "readiness_level": r.readiness_level, "sleep_hours": r.sleep_hours,
        "sleep_score": r.sleep_score, "body_battery_peak": r.body_battery_peak,
        "hrv_last_night": r.hrv_last_night, "hrv_status": r.hrv_status,
        "resting_hr": r.resting_hr,
    } for r in reversed(rows)]}


async def _typical_run_hour(session: AsyncSession) -> Optional[int]:
    """The hour (UTC) this runner usually runs — the median of their recent starts."""
    rows = (await session.execute(
        select(Activity.start_date).where(Activity.start_date.isnot(None))
        .order_by(Activity.start_date.desc()).limit(30)
    )).scalars().all()
    hours = sorted(d.hour for d in rows if d)
    return hours[len(hours) // 2] if hours else None


async def _run_location(session: AsyncSession) -> Optional[tuple[float, float]]:
    """Where the runner actually runs — taken from their most recent GPS run."""
    row = (await session.execute(
        select(Activity.start_latlng)
        .where(Activity.start_latlng.isnot(None))
        .order_by(Activity.start_date.desc()).limit(1)
    )).scalars().first()
    if not row or len(row) < 2:
        return None
    return float(row[0]), float(row[1])


async def _today_workout(session: AsyncSession, plan: Plan) -> Optional[PlannedWorkout]:
    today = datetime.utcnow().date()
    wos = (await session.execute(
        select(PlannedWorkout).where(PlannedWorkout.plan_id == plan.id)
        .order_by(PlannedWorkout.date)
    )).scalars().all()
    return next((w for w in wos if w.date.date() == today), None)


@app.get("/api/plan/today-guidance")
async def today_guidance(refresh: bool = False,
                         session: AsyncSession = Depends(get_session)):
    """Should today's session stand, and what pace does today's air allow?

    Read-only for the plan. Everything here is a proposal with its reasoning
    attached — the runner decides.
    """
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")

    w = await _today_workout(session, plan)
    day = datetime.utcnow().date().isoformat()

    assessment = await _wellness(session, day, refresh=refresh)
    recommendation = rdns.adjust(w.day_type if w else "rest", assessment)

    heat_out = None
    loc = await _run_location(session)
    if loc and w and w.day_type != "rest":
        hour = await _typical_run_hour(session)
        conds = (await weather.conditions_at_hour(*loc, hour) if hour is not None
                 else await weather.conditions(*loc))
        if conds:
            heat_out = heat.adjust(conds["temp_c"], conds["dew_point_c"],
                                   w.pace_low_sec, w.pace_high_sec)
            heat_out["humidity_pct"] = conds.get("humidity_pct")
            heat_out["feels_like_c"] = conds.get("feels_like_c")
            heat_out["cloud_cover_pct"] = conds.get("cloud_cover_pct")
            heat_out["local_hour"] = conds.get("local_hour")

    dicts = await _workout_dicts(session, plan)
    return {
        "workout": next((d for d in dicts if w and d["id"] == w.id), None),
        "readiness": assessment,
        "recommendation": recommendation,
        "heat": heat_out,
        "can_apply": bool(w and recommendation["action"] in ("downgrade", "rest")),
    }


@app.post("/api/plan/today-guidance/accept")
async def accept_today_guidance(session: AsyncSession = Depends(get_session)):
    """Apply the recommended adjustment to today's session and re-sync the watch."""
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")
    w = await _today_workout(session, plan)
    if w is None:
        raise HTTPException(status_code=404, detail="Nothing scheduled today")

    day = datetime.utcnow().date().isoformat()
    assessment = await _wellness(session, day)
    rec = rdns.adjust(w.day_type, assessment)
    if rec["action"] not in ("downgrade", "rest"):
        raise HTTPException(status_code=400,
                            detail="Nothing to adjust — today's session stands as written.")

    snap = plan.fitness_snapshot or {}
    easy_pace = snap.get("easy_pace_sec") or 450
    ceiling = snap.get("easy_hr_ceiling")
    was = w.day_type

    if rec["action"] == "rest":
        if w.garmin_workout_id:
            try:
                await garmin.remove_workout(w.garmin_workout_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not pull workout %s off the watch: %s", w.id, exc)
            w.garmin_workout_id = None
        w.day_type = "rest"
        w.title = "Rest — recovery day"
        w.description = rec["reason"]
        w.target_distance_m = None
        w.pace_low_sec = w.pace_high_sec = w.hr_ceiling = None
        w.structure = None
    else:
        km = min(3.0, (w.target_distance_m or 3000) / 1000.0)
        low, high = easy_pace - 15, easy_pace + 20
        w.day_type = "easy"
        w.title = "Easy run (eased back)"
        w.description = rec["reason"]
        w.target_distance_m = km * 1000
        w.pace_low_sec, w.pace_high_sec, w.hr_ceiling = low, high, ceiling
        w.structure = pgen._run_structure(pgen._steady_steps(km, low, high, ceiling))
        try:
            await _garmin_push(w)
        except Exception as exc:  # noqa: BLE001 — the plan change still stands
            logger.warning("Re-push after readiness downgrade failed for %s: %s", w.id, exc)

    await session.commit()
    resp = await _plan_response(session, plan)
    resp["adjustment"] = {"was": was, "now": w.day_type, "reason": rec["reason"]}
    return resp


# ---------------------------------------------------------------------------
# Plan calibration — re-aim the plan at the runner it actually has
# ---------------------------------------------------------------------------

async def _calibration(session: AsyncSession, plan: Plan) -> dict[str, Any]:
    """The full working: what the plan assumed, what the runs say, what we'd change."""
    dicts = await _plan_activity_dicts(session)
    # Compare against the plan's CURRENT bands, not the original snapshot, so a
    # second calibration doesn't re-propose an adjustment already applied.
    nxt = (await session.execute(
        select(PlannedWorkout)
        .where(PlannedWorkout.plan_id == plan.id, PlannedWorkout.day_type == "easy")
        .order_by(PlannedWorkout.date)
    )).scalars().first()
    return padapt.calibrate(
        plan.fitness_snapshot or {}, dicts, datetime.utcnow(),
        current_easy_low=nxt.pace_low_sec if nxt else None,
    )


@app.get("/api/plan/calibration")
async def get_plan_calibration(session: AsyncSession = Depends(get_session)):
    """Preview only — never writes. Shows the evidence behind every proposed change."""
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")
    out = await _calibration(session, plan)
    out["history"] = plan.calibrations or []
    return out


@app.post("/api/plan/calibration/apply")
async def apply_plan_calibration(session: AsyncSession = Depends(get_session)):
    """Re-aim the plan's remaining easy/long/strides days at the measured easy pace,
    then re-push the affected workouts so the watch agrees with the app."""
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")

    cal = await _calibration(session, plan)
    new_easy = (cal.get("proposed") or {}).get("easy_low_sec")
    if not cal["has_changes"] or new_easy is None:
        raise HTTPException(status_code=400,
                            detail="Nothing to calibrate — no change is supported by your runs yet.")
    new_easy_pace = new_easy + 15  # band low -> centre, the generator's convention

    today = datetime.utcnow().date()
    wos = (await session.execute(
        select(PlannedWorkout).where(PlannedWorkout.plan_id == plan.id)
        .order_by(PlannedWorkout.date)
    )).scalars().all()

    updated, repushed, failed = 0, 0, []
    for w in wos:
        if w.date.date() < today:      # the past is history — never rewrite it
            continue
        patch = padapt.retarget_workout(w.day_type, w.structure, new_easy_pace)
        if patch is None:
            continue
        w.pace_low_sec = patch["pace_low_sec"]
        w.pace_high_sec = patch["pace_high_sec"]
        if patch["structure"]:
            w.structure = patch["structure"]
        updated += 1
        if w.garmin_workout_id and (w.structure or {}).get("steps"):
            try:
                await _garmin_push(w)   # replaces the old upload, so no duplicates
                repushed += 1
            except Exception as exc:  # noqa: BLE001 — a Garmin hiccup must not lose the calibration
                logger.warning("Re-push after calibration failed for %s: %s", w.id, exc)
                failed.append({"id": w.id, "title": w.title})

    # The snapshot now holds a MEASURED easy pace, not the starting estimate.
    snap = dict(plan.fitness_snapshot or {})
    snap["easy_pace_sec"] = new_easy_pace
    snap["easy_pace_method"] = "measured"
    plan.fitness_snapshot = snap

    # Keep the audit trail: every adjustment stays inspectable after the fact.
    plan.calibrations = (plan.calibrations or []) + [{
        "date": today.isoformat(),
        "changes": cal["changes"],
        "insights": cal["insights"],
        "workouts_updated": updated,
    }]

    await session.commit()
    resp = await _plan_response(session, plan)
    resp["calibration"] = {"workouts_updated": updated, "repushed_to_watch": repushed,
                           "changes": cal["changes"], "failed": failed}
    return resp


@app.post("/api/plan/sync-to-watch")
async def sync_plan_to_watch(session: AsyncSession = Depends(get_session)):
    """Push every upcoming structured workout in the active plan to the Garmin watch."""
    plan = await _active_plan(session)
    if plan is None:
        raise HTTPException(status_code=404, detail="No active plan")
    today = datetime.utcnow().date()
    wos = (await session.execute(
        select(PlannedWorkout).where(PlannedWorkout.plan_id == plan.id)
        .order_by(PlannedWorkout.date)
    )).scalars().all()

    pushed, failed = 0, []
    for w in wos:
        if w.day_type == "rest" or not (w.structure or {}).get("steps"):
            continue
        if w.date.date() < today:      # don't schedule the past onto the watch
            continue
        try:
            await _garmin_push(w)
            pushed += 1
        except Exception as exc:  # noqa: BLE001 — one bad workout shouldn't abort the sync
            logger.warning("Garmin sync failed for workout %s: %s", w.id, exc)
            failed.append({"id": w.id, "title": w.title, "error": str(exc)[:120]})
    await session.commit()
    return {"pushed": pushed, "failed": failed, "total_failed": len(failed)}


@app.post("/api/plan/workout/{workout_id}/push")
async def push_workout_to_garmin(workout_id: int,
                                 session: AsyncSession = Depends(get_session)):
    """Upload this workout's steps to Garmin Connect and schedule it on its date,
    so it lands on the watch and guides the run step-by-step."""
    w = await session.get(PlannedWorkout, workout_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    if not (w.structure or {}).get("steps"):
        raise HTTPException(status_code=400,
                            detail="This workout has no structured steps to send.")
    try:
        await _garmin_push(w)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Garmin push failed for workout %s: %s", workout_id, exc)
        raise HTTPException(status_code=502, detail=f"Garmin push failed: {exc}")
    await session.commit()
    return {"garmin_workout_id": w.garmin_workout_id,
            "date": w.date.date().isoformat(), "title": w.title}


@app.delete("/api/plan/workout/{workout_id}/push")
async def remove_workout_from_garmin(workout_id: int,
                                     session: AsyncSession = Depends(get_session)):
    """Remove this workout from the Garmin calendar/watch."""
    w = await session.get(PlannedWorkout, workout_id)
    if w is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    if not w.garmin_workout_id:
        return {"garmin_workout_id": None}
    try:
        await garmin.remove_workout(w.garmin_workout_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Garmin remove failed for workout %s: %s", workout_id, exc)
        raise HTTPException(status_code=502, detail=f"Garmin remove failed: {exc}")
    w.garmin_workout_id = None
    await session.commit()
    return {"garmin_workout_id": None}
