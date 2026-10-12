"""The RunFlow MCP server — RunFlow as a Claude app connector.

Mounted into the FastAPI app at a path containing a shared secret, because
Claude app custom connectors accept only authless or OAuth servers: there is no
field for a bearer token or a custom header. The mount path *is* the credential.

Scope is read + trigger-sync. Nothing here writes training data.
"""
from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

import config

mcp = MCPServer("RunFlow")

# The transport rejects any non-localhost Host header with 421 Misdirected
# Request unless the hostname is allowlisted here. nginx forwards the public
# hostname through, so both the bare form and the any-port form are needed.
_ALLOWED_HOSTS = [
    "runflow-api.skdev.one",
    "runflow-api.skdev.one:*",
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]

asgi_app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(allowed_hosts=_ALLOWED_HOSTS),
)

MOUNT_PATH = f"/mcp/{config.MCP_SECRET}" if config.MCP_SECRET else None


import json
import logging
import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

import fitness_model as fm
import mcp_reports as mr
import mcp_views as mv
from database import async_session
from models import Activity, Stream

logger = logging.getLogger(__name__)


def _fmt_pace(sec_per_km: Optional[float]) -> str:
    if not sec_per_km:
        return "—"
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}/km"


def _fmt_dur(secs: Optional[float]) -> str:
    if secs is None:
        return "—"
    return f"{int(secs // 60)}m{int(secs % 60):02d}s"


def _json_block(value) -> str:
    """Compact JSON, heavy fields removed."""
    return json.dumps(mv.strip_heavy(value), default=str, indent=1)[:6000]


async def _streams_for(session, activity_id: int) -> dict[str, list]:
    rows = (await session.execute(
        select(Stream).where(Stream.activity_id == activity_id)
    )).scalars().all()
    return {r.stream_type: r.data for r in rows}


@mcp.tool()
async def list_recent_runs(limit: int = 10, since: Optional[str] = None) -> str:
    """Recent runs, newest first, as a compact table.

    Each row: id, date, distance, pace, average and max HR, share of time in
    Zone 5, aerobic Training Effect, the heat penalty the conditions imposed,
    and whether the run was fragmented (stopped and restarted mid-run).

    Start here for any question about recent training. Use the returned ids with
    get_run_detail or compare_runs.

    Args:
        limit: how many runs to return (1-50).
        since: optional ISO date (YYYY-MM-DD); only runs on or after it.
    """
    try:
        limit = max(1, min(50, limit))
        async with async_session() as session:
            q = select(Activity).order_by(Activity.start_date.desc())
            if since:
                try:
                    q = q.where(Activity.start_date >= datetime.fromisoformat(since))
                except ValueError:
                    return f"'{since}' is not an ISO date — use YYYY-MM-DD."
            acts = (await session.execute(q.limit(limit))).scalars().all()
            if not acts:
                return "No runs found for that range."

            lines = ["id | date | dist | pace | avgHR | maxHR | Z5% | TE | "
                     "terrain | heat | notes"]
            for a in acts:
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if (km and a.moving_time) else None
                shares = mv.zone_shares(a.hr_zones)
                st = await _streams_for(session, a.id)
                pauses = mv.find_pauses(st.get("time"), st.get("distance"))
                ter = mv.terrain(a.total_elevation_gain, a.elev_high,
                                 a.elev_low, a.distance)
                flags = []
                if pauses:
                    flags.append(f"fragmented ({len(pauses)} pauses)")
                if a.notes:
                    flags.append("has note")
                notes = "; ".join(flags)
                lines.append(
                    f"{a.id} | {a.start_date:%Y-%m-%d} | {km:.2f}km | "
                    f"{_fmt_pace(pace)} | {mv.num(a.average_heartrate)} | "
                    f"{mv.num(a.max_heartrate)} | {mv.num(shares.get(5))}% | "
                    f"{mv.num(a.aerobic_te)} {a.training_effect_label or ''} | "
                    f"{(ter or {}).get('label', '—')} | "
                    f"{mv.num(a.heat_penalty_sec)} s/km | {notes}"
                )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001 — a raising tool gives Claude nothing
        logger.exception("list_recent_runs failed")
        return f"Could not read recent runs: {exc}"


@mcp.tool()
async def get_run_detail(run_id: int) -> str:
    """Full analysis of one run.

    Returns 500 m splits computed on moving time (so pauses do not distort the
    pace), mean and peak HR per split, cardiac drift, how long it took to reach
    Zone 4 and Zone 5, the zone breakdown, every pause with the HR going in and
    coming out, running dynamics, and the weather with the cool-day equivalent
    pace.

    Read cardiac drift alongside the zone shares, never alone: a run that starts
    genuinely easy reports higher drift than one that was hard throughout,
    because the first-half mean is lower. High drift with a falling Zone 5 share
    is improvement, not regression.

    Args:
        run_id: the activity id, from list_recent_runs.
    """
    try:
        async with async_session() as session:
            a = await session.get(Activity, run_id)
            if a is None:
                return f"Run {run_id} not found."
            st = await _streams_for(session, run_id)
            hr, time, dist = st.get("heartrate"), st.get("time"), st.get("distance")

            km = (a.distance or 0) / 1000.0
            pace = (a.moving_time / km) if (km and a.moving_time) else None
            cad = round(a.average_cadence, 1) if a.average_cadence else "—"
            out = [
                f"{a.name or 'Run'} — {a.start_date:%Y-%m-%d %H:%M}",
                f"{km:.2f} km in {_fmt_dur(a.moving_time)} at {_fmt_pace(pace)}",
                f"HR avg {mv.num(a.average_heartrate)} / max "
                f"{mv.num(a.max_heartrate)} · cadence {cad}",
                f"Training Effect {mv.num(a.aerobic_te)} aerobic /"
                f" {mv.num(a.anaerobic_te)} anaerobic "
                f"{a.training_effect_label or ''}",
            ]

            if a.notes:
                out.append(f"Note: {a.notes}")

            shares = mv.zone_shares(a.hr_zones)
            if shares:
                out.append("Zones: " + "  ".join(
                    f"Z{z} {shares[z]}%" for z in sorted(shares)
                ))
                legend = mv.zone_legend(a.hr_zones)
                if legend:
                    out.append(f"  Boundaries (bpm): {legend}")
            mpb = mv.metres_per_beat(a.distance, a.moving_time, a.average_heartrate)
            if mpb:
                out.append(f"Aerobic efficiency: {mpb} m/beat")

            drift = mv.cardiac_drift(hr)
            if drift is not None:
                out.append(f"Cardiac drift: {drift:+.1f} bpm (first half vs second)")
            z4 = mv.seconds_to_cross(hr, time, 168)
            z5 = mv.seconds_to_cross(hr, time, 189)
            out.append(
                f"Crossed Z4 at {_fmt_dur(z4)}, Z5 at {_fmt_dur(z5)}"
                if (z4 is not None or z5 is not None) else "Never left Z3."
            )

            splits = mv.split_table(dist, time, hr)
            if splits:
                out += ["", "500 m splits (moving time):"]
                for s in splits:
                    out.append(
                        f"  {s['from_m']:>4}-{s['to_m']:<4}m  "
                        f"{_fmt_pace(s['pace_sec_per_km'])}  "
                        f"HR {mv.num(s['hr_avg'])} (peak {mv.num(s['hr_peak'])})"
                    )

            pauses = mv.find_pauses(time, dist, hr)
            if pauses:
                out += ["", f"Stopped {len(pauses)} time(s) — this was not a "
                            "continuous run:"]
                for p in pauses:
                    out.append(
                        f"  at {p['at_km']:.2f} km — paused {_fmt_dur(p['seconds'])}"
                        f" (HR {mv.num(p['hr_in'])} in, {mv.num(p['hr_out'])} out)"
                    )

            rd = a.running_dynamics or {}
            if rd:
                out += ["", (
                    f"Dynamics: stride {mv.num(rd.get('stride_length'), 2)} cm"
                    f" · ground contact "
                    f"{mv.num(rd.get('ground_contact_time'), 1)} ms · vertical "
                    f"oscillation {mv.num(rd.get('vertical_oscillation'), 2)} cm"
                )]

            ter = mv.terrain(a.total_elevation_gain, a.elev_high,
                             a.elev_low, a.distance)
            if ter:
                out += ["", (
                    f"Terrain: {ter['label']} — {mv.num(ter['gain_m'])} m gain"
                    f" ({mv.num(ter['gain_per_km'])} m/km), elevation range "
                    f"{mv.num(ter['range_m'])} m"
                )]
                if ter["label"] == "flat":
                    out.append(
                        "  Flat ground: a climbing heart rate here is effort and "
                        "drift, with no terrain to attribute it to."
                    )
            if a.start_latlng:
                line = f"Started at {a.start_latlng[0]:.5f}, {a.start_latlng[1]:.5f}"
                prev = (await session.execute(
                    select(Activity)
                    .where(Activity.start_date < a.start_date,
                           Activity.start_latlng.isnot(None))
                    .order_by(Activity.start_date.desc()).limit(1)
                )).scalars().first()
                if prev is not None:
                    same = mv.same_place(a.start_latlng, prev.start_latlng)
                    if same is True:
                        line += " — same place as the previous run"
                    elif same is False:
                        line += " — a different place from the previous run"
                out.append(line)

            if a.dew_point_c is not None:
                out += ["", (
                    f"Conditions: {mv.num(a.temp_c)}°C, dew point "
                    f"{mv.num(a.dew_point_c)}°C (index {mv.num(a.heat_index)})"
                    f" — cost ~{mv.num(a.heat_penalty_sec)} s/km;"
                    f" cool-day equivalent {_fmt_pace(a.normalized_pace_sec)}"
                )]
            return mv.cap_text("\n".join(out))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_run_detail failed")
        return f"Could not analyse run {run_id}: {exc}"


@mcp.tool()
async def compare_runs(run_ids: list[int]) -> str:
    """Two to five runs side by side on the metrics that matter.

    Distance, pace, average HR, Zone 5 share, cardiac drift, Training Effect,
    dew point and weather-normalized pace. Use this to answer "am I improving?".
    The pause count is shown too, because comparing a fragmented run against a
    continuous one is misleading — pauses deflate both average HR and pace.

    Args:
        run_ids: 2-5 activity ids from list_recent_runs.
    """
    try:
        if len(run_ids) > 5:
            return "Compare at most 5 runs at a time."
        if len(run_ids) < 2:
            return "Give at least 2 run ids to compare."
        async with async_session() as session:
            lines = ["id | date | dist | pace | avgHR | Z5% | drift | TE | dew | "
                     "norm pace | pauses"]
            for rid in run_ids:
                a = await session.get(Activity, rid)
                if a is None:
                    lines.append(f"{rid} | not found")
                    continue
                st = await _streams_for(session, rid)
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if (km and a.moving_time) else None
                shares = mv.zone_shares(a.hr_zones)
                drift = mv.cardiac_drift(st.get("heartrate"))
                pauses = mv.find_pauses(st.get("time"), st.get("distance"))
                lines.append(
                    f"{a.id} | {a.start_date:%Y-%m-%d} | {km:.2f}km | "
                    f"{_fmt_pace(pace)} | {mv.num(a.average_heartrate)} | "
                    f"{mv.num(shares.get(5))}% | "
                    f"{f'{drift:+.1f}' if drift is not None else '—'} | "
                    f"{mv.num(a.aerobic_te)} | {mv.num(a.dew_point_c)}°C | "
                    f"{_fmt_pace(a.normalized_pace_sec)} | {len(pauses)}"
                )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("compare_runs failed")
        return f"Could not compare those runs: {exc}"


@mcp.tool()
async def get_recovery(days: int = 14) -> str:
    """Recovery markers per day: readiness, sleep, body battery, HRV, resting HR.

    The resting-HR trend is the most reliable single signal of whether training
    is being absorbed; HRV status and body-battery peak corroborate it.

    Important caveat, repeated in the output: on days with a run the stored
    readiness score was captured AFTER the run. The auto-sync refreshes the row
    every two hours, so the value kept is the last one before UTC midnight,
    roughly 22 hours after a morning run. A score of 1-5 on a run day is
    therefore an artifact of the session just completed, not a verdict on how
    ready the athlete was that morning.

    Args:
        days: how many days back to return (1-90).
    """
    try:
        days = max(1, min(90, days))
        from models import DailyWellness
        async with async_session() as session:
            rows = (await session.execute(
                select(DailyWellness).order_by(DailyWellness.date.desc()).limit(days)
            )).scalars().all()
            if not rows:
                return "No wellness data recorded."
            lines = ["date | readiness | sleep | battery | HRV | restingHR"]
            for w in reversed(rows):
                lines.append(
                    f"{w.date} | {w.readiness_score} {w.readiness_level or ''} | "
                    f"{w.sleep_hours}h (score {w.sleep_score}) | "
                    f"{w.body_battery_peak} | {w.hrv_last_night} "
                    f"{w.hrv_status or ''} | {w.resting_hr}"
                )
            lines += ["", (
                "Caveat: on run days the readiness score was captured after the "
                "run, so a very low value there reflects the session just "
                "completed rather than the athlete's state that morning."
            )]
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_recovery failed")
        return f"Could not read recovery data: {exc}"


@mcp.tool()
async def get_records() -> str:
    """Personal records by distance, plus the sprint baseline.

    PRs for 1k/2k/3k/5k/10k with the date and pace, the best single 1 km split,
    and the sprint profile (best 100 m and 200 m, top speed, fade percentage and
    the resulting diagnosis).
    """
    try:
        import main as _main
        async with async_session() as session:
            prs = await _main.personal_records(session)
            base = await _main.sprint_baseline_endpoint(session)
            return mv.cap_text(
                "Personal records:\n" + _json_block(prs)
                + "\n\nSprint baseline:\n" + _json_block(base)
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_records failed")
        return f"Could not read records: {exc}"


@mcp.tool()
async def get_training_context() -> str:
    """Where the athlete is in their training right now.

    A summary, not a data dump: the current training phase and the one before
    it, the active plan, today's session with the readiness-based
    recommendation, and progress toward the sub-6:00/km gate — reported both
    raw and weather-normalized, because dew point has been costing 20+ s/km.

    Use get_weekly_volume for week-by-week detail and get_records for PRs.
    """
    try:
        import main as _main
        out = []
        async with async_session() as session:
            # gap_days must be passed explicitly: its endpoint default is a
            # FastAPI Query object, only resolved by the HTTP layer.
            phases = await _main.get_phases(gap_days=14, session=session)
            rows = (phases or {}).get("phases") or []
            if rows:
                cur = rows[-1]
                out.append(
                    f"Current phase {cur.get('phase_number')}: started "
                    f"{str(cur.get('start_date'))[:10]}, "
                    f"{cur.get('duration_days')} days, {cur.get('total_runs')} runs, "
                    f"{cur.get('total_distance_km')} km, "
                    f"{cur.get('runs_per_week')} runs/week, avg pace "
                    f"{_fmt_pace(cur.get('avg_pace_sec_per_km'))}"
                )
                if cur.get("break_before_days"):
                    out.append(
                        f"  Preceded by a {cur['break_before_days']}-day break."
                    )
                if len(rows) > 1:
                    prev = rows[-2]
                    out.append(
                        f"Previous phase {prev.get('phase_number')}: "
                        f"{prev.get('total_runs')} runs, "
                        f"{prev.get('total_distance_km')} km, "
                        f"{prev.get('runs_per_week')} runs/week, avg pace "
                        f"{_fmt_pace(prev.get('avg_pace_sec_per_km'))}"
                    )
                out.append(f"({len(rows)} phases on record since 2019.)")

            plan = await _main.get_active_plan(session)
            p = (plan or {}).get("plan")
            if p:
                target = p.get("sprint_target_sec") or p.get("target_time_sec")
                out += ["", (
                    f"Active plan #{p.get('id')}: {p.get('goal_type')}, target "
                    f"{target}, started {str(p.get('start_date'))[:10]}, goal date "
                    f"{str(p.get('goal_date'))[:10]}, {p.get('weeks')} weeks, "
                    f"status {p.get('status')}"
                )]
                prof = p.get("profile") or {}
                if prof.get("diagnosis"):
                    out.append(f"  Diagnosis: {prof['diagnosis']}")
            else:
                out += ["", "No active plan."]

            try:
                g = await _main.today_guidance(session=session)
            except Exception as exc:  # noqa: BLE001 — needs the watch, may be absent
                g = None
                out += ["", f"Today's guidance unavailable: {exc}"]
            if g:
                w = (g.get("workout") or {}).get("workout") or g.get("workout") or {}
                rec = g.get("recommendation") or {}
                rd = g.get("readiness") or {}
                out += ["", (
                    f"Today: planned \"{w.get('title', '—')}\" "
                    f"({w.get('day_type', '—')})"
                )]
                out.append(
                    f"  Recommendation: {rec.get('action', '—')} — "
                    f"{rec.get('reason', '')}"
                )
                out.append(
                    f"  Readiness {rd.get('score')} ({rd.get('level')}). Note: on a "
                    "day the athlete has already run, this score was captured "
                    "after the run and understates their morning state."
                )

            acts = (await session.execute(
                select(Activity)
                .where(Activity.moving_time.isnot(None), Activity.distance > 1000)
                .order_by(Activity.start_date.desc()).limit(15)
            )).scalars().all()
            best_raw = min(
                (a.moving_time / (a.distance / 1000.0) for a in acts), default=None
            )
            best_norm = min(
                (a.normalized_pace_sec for a in acts if a.normalized_pace_sec),
                default=None,
            )
            out += ["", (
                f"Gate (sub-6:00/km): best of the last {len(acts)} runs is "
                f"{_fmt_pace(best_raw)} raw, {_fmt_pace(best_norm)} "
                "weather-normalized."
            )]
        return mv.cap_text("\n".join(out))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_training_context failed")
        return f"Could not read training context: {exc}"


import asyncio

# Garmin's import pages through the activity list, so it can outlast a tool
# call. Bounded, but the work is shielded so it genuinely continues server-side
# past the timeout rather than being cancelled half-done.
SYNC_TIMEOUT_SEC = 60.0


async def _garmin_sync(session):
    """Indirection so tests can substitute the import without touching Garmin."""
    import main as _main
    return await _main.import_garmin_sync(session)


@mcp.tool()
async def sync_garmin() -> str:
    """Pull any new runs from Garmin into RunFlow.

    The only action in this connector — everything else is read-only. Safe to
    call repeatedly: the import is deduplicated by Garmin activity id and stops
    once it reaches runs already stored.

    Returns the number of runs imported. If the import outlasts the timeout it
    keeps running on the server and the result says so — call list_recent_runs a
    minute later to see what landed.
    """
    async def _run():
        async with async_session() as session:
            return await _garmin_sync(session)

    task = asyncio.create_task(_run())
    try:
        # shield so a timeout leaves the import running instead of cancelling a
        # half-finished pass over the Garmin activity list.
        res = await asyncio.wait_for(asyncio.shield(task), timeout=SYNC_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        task.add_done_callback(
            lambda t: logger.info("background garmin sync finished: %s",
                                  t.exception() or t.result())
        )
        return (
            "Sync still running on the server — it outlasted the "
            f"{int(SYNC_TIMEOUT_SEC)}s tool timeout but was not cancelled. "
            "Call list_recent_runs shortly to see what was imported."
        )
    except Exception as exc:  # noqa: BLE001 — a raising tool gives Claude nothing
        logger.exception("sync_garmin failed")
        return f"Garmin sync failed: {exc}"

    res = res or {}
    imported = res.get("imported", 0)
    err = res.get("error")
    return f"Imported {imported} new run(s) from Garmin." + (
        f" Warning: {err}" if err else ""
    )


def _get_route_index() -> list[str]:
    """Every registered GET path under /api, read from the app's own route table.

    Generated rather than hand-listed so it can never drift from the real API.
    """
    import main as _main
    paths = set()
    for r in _main.app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if "GET" in methods and path.startswith("/api"):
            paths.add(path)
    return sorted(paths)


def _thin_streams(value):
    """Downsample stream-shaped lists rather than dropping them."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "data" and isinstance(v, list) and len(v) > mv.MAX_STREAM_POINTS:
                out[k] = mv.downsample(v)
            else:
                out[k] = _thin_streams(v)
        return out
    if isinstance(value, list):
        return [_thin_streams(v) for v in value]
    return value


@mcp.tool()
async def call_api(
    path: str,
    params: Optional[dict] = None,
    include_heavy: bool = False,
) -> str:
    """Last resort: call a RunFlow REST endpoint directly.

    PREFER THE SPECIFIC TOOLS. list_recent_runs, get_run_detail, review_run,
    compare_runs, get_recovery, get_records, get_training_context,
    get_fitness_model, get_training_block and get_plan_status return analysed
    summaries — splits on moving time, cardiac drift, zone shares, pauses,
    weather-normalized pace, volume per month — that this tool does not
    compute. Use call_api only when none of them covers the question, for
    example a route-level breakdown or the wellness history.

    GET routes only, so nothing here can change data. Encoded GPS polylines and
    raw sample streams are removed unless include_heavy is true, and streams are
    then thinned to at most 200 points (the true sample count is reported).
    Output is capped at 25,000 characters.

    Args:
        path: the route path including the /api prefix, e.g. "/api/routes".
            A leading slash is optional.
        params: optional query parameters.
        include_heavy: include polylines and downsampled streams.
    """
    try:
        import httpx
        import main as _main

        if not path.startswith("/"):
            path = "/" + path
        index = _get_route_index()
        if path not in index:
            near = [p for p in index if p.split("/")[:3] == path.split("/")[:3]]
            hint = ", ".join(near[:8]) if near else ", ".join(index[:12])
            return (
                f"No GET route '{path}'. Closest available: {hint}\n\n"
                "Note: POST routes are not callable through this tool — it is "
                "read-only by design. Use sync_garmin for the one action."
            )

        transport = httpx.ASGITransport(app=_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://runflow.internal"
        ) as client:
            resp = await client.get(path, params=params or {}, timeout=30.0)
        if resp.status_code != 200:
            return f"{path} returned HTTP {resp.status_code}: {resp.text[:400]}"

        data = resp.json()
        data = _thin_streams(data) if include_heavy else mv.strip_heavy(data)
        return mv.cap_text(json.dumps(data, default=str, indent=1))
    except Exception as exc:  # noqa: BLE001
        logger.exception("call_api failed for %s", path)
        return f"Could not call {path}: {exc}"


@mcp.tool()
async def get_aerobic_trend(days: int = 90) -> str:
    """Aerobic efficiency over time — is the same heart rate buying more distance?

    Reports metres covered per heartbeat for each run, alongside pace and
    average HR. Rising metres-per-beat across weeks is what base training is
    supposed to produce, and it is a better progress signal than raw pace
    because it is not fooled by a hard effort.

    The figure is confounded by heat, so the weather-normalized pace is shown
    beside it: in a 24-26 C dew point raw pace understates fitness by 20+ s/km.

    Args:
        days: how far back to look (1-365).
    """
    try:
        days = max(1, min(365, days))
        cutoff = datetime.utcnow() - timedelta(days=days)
        async with async_session() as session:
            acts = (await session.execute(
                select(Activity)
                .where(Activity.start_date >= cutoff,
                       Activity.average_heartrate.isnot(None),
                       Activity.moving_time.isnot(None),
                       Activity.distance > 500)
                .order_by(Activity.start_date)
            )).scalars().all()
            if not acts:
                return f"No runs with heart-rate data in the last {days} days."

            lines = ["date | dist | pace | avgHR | m/beat | norm pace | terrain"]
            for a in acts:
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if km else None
                mpb = mv.metres_per_beat(a.distance, a.moving_time,
                                         a.average_heartrate)
                ter = mv.terrain(a.total_elevation_gain, a.elev_high,
                                 a.elev_low, a.distance)
                lines.append(
                    f"{a.start_date:%Y-%m-%d} | {km:.2f}km | {_fmt_pace(pace)} | "
                    f"{mv.num(a.average_heartrate)} | {mv.num(mpb, 2)} | "
                    f"{_fmt_pace(a.normalized_pace_sec)} | "
                    f"{(ter or {}).get('label', '—')}"
                )
            vals = [mv.metres_per_beat(a.distance, a.moving_time,
                                       a.average_heartrate) for a in acts]
            vals = [v for v in vals if v]
            if len(vals) >= 4:
                half = len(vals) // 2
                first = sum(vals[:half]) / half
                second = sum(vals[half:]) / len(vals[half:])
                lines += ["", (
                    f"Trend: {round(first, 2)} -> {round(second, 2)} m/beat "
                    f"(older half vs newer half) — "
                    f"{'improving' if second > first else 'flat or declining'}."
                )]
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_aerobic_trend failed")
        return f"Could not read the aerobic trend: {exc}"


@mcp.tool()
async def get_weekly_volume(weeks: int = 8) -> str:
    """Training volume per ISO week — runs, distance, time, pace and Zone 5 share.

    Answers "am I training consistently?" directly, which the phase summary in
    get_training_context buries. The Zone 5 share is time-weighted across the
    week, so a long hard run counts more than a short one.

    Args:
        weeks: how many recent weeks to return (1-52).
    """
    try:
        weeks = max(1, min(52, weeks))
        cutoff = datetime.utcnow() - timedelta(weeks=weeks)
        async with async_session() as session:
            acts = (await session.execute(
                select(Activity)
                .where(Activity.start_date >= cutoff,
                       Activity.moving_time.isnot(None), Activity.distance > 0)
                .order_by(Activity.start_date)
            )).scalars().all()
            if not acts:
                return f"No runs in the last {weeks} weeks."
            rows = [{
                "start_date": a.start_date,
                "distance": a.distance,
                "moving_time": a.moving_time,
                "z5": mv.zone_shares(a.hr_zones).get(5),
            } for a in acts]
            lines = ["week | runs | km | minutes | mean pace | Z5%"]
            for b in mv.weekly_buckets(rows):
                lines.append(
                    f"{b['year']}-W{b['week']:02d} | {b['runs']} | {b['km']} | "
                    f"{b['minutes']} | {_fmt_pace(b['pace_sec_per_km'])} | "
                    f"{mv.num(b['z5_pct'])}%"
                )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_weekly_volume failed")
        return f"Could not read weekly volume: {exc}"


@mcp.tool()
async def set_run_note(run_id: int, note: str) -> str:
    """Record the athlete's own context on a run.

    THE ONLY WRITE in this connector. Use it for what the watch cannot know and
    the athlete just told you: the surface ("flat stadium track"), how the body
    felt ("hip tight afterwards"), what the session was for, why it was cut
    short. That context is what makes a later read correct — a flat track run
    and a hill route are indistinguishable from pace and HR alone.

    Write a note when the athlete volunteers something a future reader would
    need. Do not invent or infer one, and do not paraphrase your own analysis
    into it: the note is their record, not yours. Replaces any existing note on
    that run; an empty string clears it.

    Args:
        run_id: the activity id, from list_recent_runs.
        note: the text to store (max 2000 characters). Empty clears it.
    """
    try:
        text = (note or "").strip()
        if len(text) > config.NOTE_MAX_CHARS:
            return (
                f"Note too long: {len(text)} characters, maximum "
                f"{config.NOTE_MAX_CHARS}. Trim it and try again."
            )
        async with async_session() as session:
            a = await session.get(Activity, run_id)
            if a is None:
                return f"Run {run_id} not found."
            a.notes = text or None
            await session.commit()
            if not text:
                return f"Note cleared on run {run_id}."
            return f"Note saved on run {run_id} ({a.start_date:%Y-%m-%d}): {text}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_run_note failed")
        return f"Could not save the note on run {run_id}: {exc}"


async def _planned_for(session, run_id: int) -> Optional[dict]:
    """The planned session this run fulfilled, graded — or None.

    Indirection so tests can substitute the adherence machinery, and so the
    lazy `main` import stays in one place.
    """
    import main as _main
    plan = await _main._active_plan(session)
    if plan is None:
        return None
    workouts = await _main._workout_dicts(session, plan)
    acts = await _main._plan_activity_dicts(session)
    graded = _main.padh.match_and_grade(workouts, acts, datetime.utcnow(), plan.start_date)
    # match_and_grade puts the matched run under `actual`, keyed activity_id.
    return next((w for w in graded["workouts"]
                 if (w.get("actual") or {}).get("activity_id") == run_id), None)


@mcp.tool()
async def review_run(run_id: int, window_sec: int = 60) -> str:
    """One run in full depth: splits, how HR behaved over time, and planned vs actual.

    Use this when the athlete asks how a specific run went, or when a question
    needs more than the summary row list_recent_runs gives — "did I fade?",
    "what HR was I at for the first five minutes?", "was that continuous?".

    Returns distance, pace and HR; whether the run was continuous or fragmented,
    with each pause located and the HR either side of it; 500 m splits on moving
    time; mean and peak HR per window across the run; cardiac drift; terrain;
    time in zones with the boundaries; the athlete's own note; and, when the run
    matched a session in the active plan, the targets beside the actuals.

    Args:
        run_id: the activity id, from list_recent_runs.
        window_sec: HR profile window in seconds (10-600). 60 shows minute by
            minute, which is the right grain for watching HR drift on an easy
            run. Use 300 for a long run to keep the table short.
    """
    try:
        window_sec = max(10, min(600, window_sec))
        async with async_session() as session:
            a = await session.get(Activity, run_id)
            if a is None:
                return f"Run {run_id} not found."
            run = {
                "id": a.id, "name": a.name, "start_date": a.start_date,
                "distance": a.distance, "moving_time": a.moving_time,
                "elapsed_time": a.elapsed_time,
                "average_heartrate": a.average_heartrate,
                "max_heartrate": a.max_heartrate,
                "average_cadence": a.average_cadence,
                "total_elevation_gain": a.total_elevation_gain,
                "elev_high": a.elev_high, "elev_low": a.elev_low,
                "hr_zones": a.hr_zones, "temp_c": a.temp_c,
                "dew_point_c": a.dew_point_c, "notes": a.notes,
            }
            streams = await _streams_for(session, run_id)
            planned = None
            planned_error = None
            try:
                planned = await _planned_for(session, run_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("adherence lookup failed for run %s", run_id)
                # An exception with an empty str() must still read as a failed
                # check, and a SQLAlchemy error can carry a whole statement
                # with its parameters — neither belongs in a run report.
                planned_error = (str(exc) or exc.__class__.__name__)[:200]
            return mr.run_review(run, streams, planned=planned,
                                 window_sec=window_sec, planned_error=planned_error)
    except Exception as exc:  # noqa: BLE001
        logger.exception("review_run failed")
        return f"Could not review run {run_id}: {exc}"


ACWR_RISK_THRESHOLD = 1.5
# Where a ratio starts being elevated at all. fitness_model.build_warnings
# gates its comeback/spike split on this before it looks at the baseline; the
# tool mirrors that order so the two cannot drift apart again.
ACWR_ELEVATED = 1.3


async def _fitness_model(session) -> dict:
    """Indirection so tests can substitute the model build."""
    import main as _main
    dicts = await _main._plan_activity_dicts(session)
    return _main.fmodel.build_fitness_model(dicts, datetime.utcnow())


@mcp.tool()
async def get_fitness_model() -> str:
    """The numbers every training decision should be sized from.

    Call this before recommending any change in volume or intensity, and before
    interpreting an "easy" run that came out hard.

    Two fields decide most arguments. `easy_pace_method` says whether the easy
    pace band was measured from recent easy running or is a stale fallback — a
    stale band is not evidence about the athlete. And the acute:chronic load
    ratio compares the last 7 days against the 28-day average: above roughly
    1.5 is the conventional injury-risk threshold and the honest recommendation
    is then to hold volume flat — but only once the baseline is real. On a thin
    baseline, under 5 km/week of chronic load, a high ratio is inflated by
    design and the 1.5 threshold does not apply.
    """
    try:
        async with async_session() as session:
            m = await _fitness_model(session)
            acwr = m.get("acwr")
            chronic = m.get("chronic_load_28d_km")
            thin = chronic is not None and chronic < fm.THIN_BASELINE_KM
            # Boundary map, so the next reader sees the relationship rather
            # than rediscovering a disagreement between two surfaces:
            # fitness_model.build_warnings raises its danger-level load warning
            # above ACWR_ELEVATED (1.3) and reframes it as a comeback note
            # below THIN_BASELINE_KM of chronic load. This chain mirrors that —
            # ratio first, baseline second — and keeps ACWR_RISK_THRESHOLD
            # (1.5) for the "hold volume flat" verdict. Between the two marks
            # on a real baseline the app flags a spike, so this cannot report
            # "within the threshold" there.
            if acwr is None:
                verdict = "not enough history to judge"
            elif acwr > ACWR_ELEVATED and thin:
                verdict = (f"thin baseline (chronic load under {fm.THIN_BASELINE_KM:g} "
                           f"km/week): the {ACWR_RISK_THRESHOLD} risk threshold does not apply yet; "
                           "the ratio is inflated by design, a comeback ramp and not a spike — keep weekly increases to 10% or less")
            elif acwr > ACWR_RISK_THRESHOLD:
                verdict = (f"ABOVE the {ACWR_RISK_THRESHOLD} risk threshold — "
                           "hold volume flat, do not add")
            elif thin:
                # Not elevated and the baseline is too small to divide by: the
                # ratio says nothing either way, so neither does this line.
                verdict = (f"thin baseline (chronic load under {fm.THIN_BASELINE_KM:g} "
                           "km/week): too little chronic load for the ratio to carry "
                           "much meaning in either direction")
            elif acwr > ACWR_ELEVATED:
                verdict = (f"elevated — past the {ACWR_ELEVATED} mark where RunFlow "
                           f"flags a load spike, below the {ACWR_RISK_THRESHOLD} risk "
                           "threshold; worth not adding more this week")
            else:
                verdict = f"within the {ACWR_RISK_THRESHOLD} risk threshold"
            method = m.get("easy_pace_method")
            pace = m.get("easy_pace_sec")
            if method == "estimate":
                kind = "ESTIMATE, not measured — no recent easy-HR runs to measure from"
                if pace is not None and pace >= fm.EASY_PACE_FLOOR_SEC:
                    kind += f"; this is the {_fmt_pace(fm.EASY_PACE_FLOOR_SEC)} floor clamp"
            elif method == "measured_stale":
                kind = "measured but STALE — adjusted for detraining since the last easy runs"
            elif method == "measured":
                kind = "measured from recent easy-HR runs"
            else:
                kind = f"{method} — no usable easy pace"
            if method in ("measured", "measured_stale") and m.get("easy_pace_floored"):
                floor = _fmt_pace(fm.EASY_PACE_FLOOR_SEC)
                detrained = m.get("detrained_easy_pace_sec")
                if method == "measured_stale" and detrained is not None:
                    # The stale branch clamps measured x decay, not the
                    # measurement: a 6:30 measurement cannot have been clamped
                    # by a 7:30 floor, and naming it as the clamped quantity
                    # made the sentence impossible.
                    days = m.get("staleness_days")
                    span = f" for {days} days of detraining" if days is not None else ""
                    kind += (f"; measured at {_fmt_pace(m.get('measured_easy_pace_sec'))}, "
                             f"adjusted to {_fmt_pace(detrained)}{span}, "
                             f"then clamped to the {floor} floor "
                             "— the real easy pace is slower than shown")
                else:
                    kind += (f"; measured at {_fmt_pace(m.get('measured_easy_pace_sec'))} "
                             f"and clamped to the {floor} floor "
                             "— the real easy pace is slower than shown")
            lines = [
                "Fitness model",
                f"  max HR: {mv.num(m.get('athlete_max_hr'))}"
                f"   easy HR ceiling: {mv.num(m.get('easy_hr_ceiling'))}",
                f"  easy pace: {_fmt_pace(pace)}  ({kind})",
                f"  threshold pace: {_fmt_pace(m.get('threshold_pace_sec'))}",
                "",
                f"  28-day volume: {mv.num(m.get('weekly_volume_28d_km'))} km/week",
                f"  longest run, 28d: {mv.num(m.get('longest_run_28d_km'))} km"
                f"   90d: {mv.num(m.get('longest_run_90d_km'))} km",
                f"  acute load (7d): {mv.num(m.get('acute_load_7d_km'))} km"
                f"   chronic (28d avg): {mv.num(m.get('chronic_load_28d_km'))} km",
                f"  ACWR: {mv.num(acwr, 2)} — {verdict}",
                "",
                f"  runs on record: {m.get('total_runs')}"
                f"   with HR data: {m.get('hr_coverage')}",
            ]
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_fitness_model failed")
        return f"Could not read the fitness model: {exc}"


# Bounds the table by construction: ~65 chars/row x 300 stays well under the
# 25,000-character cap, so the cap never silently drops the newest rows.
TRAINING_BLOCK_MAX_ROWS = 300


def _weeks_covered(year: int, month: int, range_start: date,
                   range_end: date, today: date) -> float:
    """Weeks of calendar actually spanned by a month inside the query range.

    km / 4.35 assumes a complete month, which understates an in-progress one
    badly: October 2026 twelve days in showed 2.8 km/wk against a true 7.0.
    That column is the "now" side of a historical comparison, so the error
    makes a current block look lighter than it is.

    Floored at one week, not one day: a run on the 1st of the month would
    otherwise read as seven times its distance per week. The floor also keeps
    the result finite when the span is empty or negative.
    """
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    first = max(month_start, range_start)
    last = min(month_end, range_end, today)
    return max(7, (last - first).days + 1) / 7.0


@mcp.tool()
async def get_training_block(start: str, end: str, by: str = "month") -> str:
    """Volume, pace and intensity per month or week over any date range.

    Use this to compare training blocks that are years apart — which is the only
    honest way to answer "have I got worse?". A single run says nothing; a month
    of them against the same month in a previous block says a great deal, and
    the usual answer is that the difference is volume rather than ability.

    Aggregates server-side, so a multi-year range costs one small response
    instead of hundreds of rows.

    Monthly buckets carry both a mean and a median pace. Prefer the median: one
    long slow run drags the mean, while the median is closer to what the block
    felt like day to day. `days_run` beside `runs` matters too, because thirty
    runs across twenty-five days is a different block from thirty across thirty.

    Args:
        start: ISO date (YYYY-MM-DD), inclusive.
        end: ISO date (YYYY-MM-DD), inclusive.
        by: "month" (default) or "week".
    """
    try:
        try:
            lo = datetime.fromisoformat(start)
            hi = datetime.fromisoformat(end) + timedelta(days=1)
        except ValueError:
            return (f"'{start}' / '{end}' must be ISO dates in the form "
                    "YYYY-MM-DD.")
        if hi <= lo:
            return (f"The end date {end} is before the start date {start} — "
                    "swap them.")
        if by not in ("month", "week"):
            return f"'{by}' is not a granularity — use \"month\" or \"week\"."
        async with async_session() as session:
            acts = (await session.execute(
                select(Activity)
                .where(Activity.start_date >= lo, Activity.start_date < hi,
                       Activity.moving_time.isnot(None), Activity.distance > 0)
                .order_by(Activity.start_date)
            )).scalars().all()
            if not acts:
                return f"No runs between {start} and {end}."
            rows = [{
                "start_date": a.start_date,
                "distance": a.distance,
                "moving_time": a.moving_time,
                "z5": mv.zone_shares(a.hr_zones).get(5),
            } for a in acts]

            buckets = (mv.weekly_buckets(rows) if by == "week"
                       else mv.monthly_buckets(rows))
            if len(buckets) > TRAINING_BLOCK_MAX_ROWS:
                return (f"{len(buckets)} {by}s with runs between {start} and {end} "
                        f"is more than the {TRAINING_BLOCK_MAX_ROWS} this table "
                        "shows. Use a narrower range"
                        + (' or by="month".' if by == "week" else "."))

            if by == "week":
                lines = ["period | runs | km | minutes | mean pace | Z5%"]
                for b in buckets:
                    lines.append(
                        f"{b['year']}-W{b['week']:02d} | {b['runs']} | {b['km']} | "
                        f"{b['minutes']} | "
                        f"{_fmt_pace(b['pace_sec_per_km'])} | {mv.num(b['z5_pct'])}"
                    )
            else:
                lines = ["period | runs | days run | km | km/wk | "
                         "median pace | mean pace | Z5%"]
                range_end = hi.date() - timedelta(days=1)
                today = datetime.utcnow().date()
                for b in buckets:
                    lines.append(
                        f"{b['year']}-{b['month']:02d} | {b['runs']} | "
                        f"{b['days_run']} | {b['km']} | "
                        f"{mv.num(b['km'] / _weeks_covered(b['year'], b['month'], lo.date(), range_end, today))} | "
                        f"{_fmt_pace(b['median_pace_sec_per_km'])} | "
                        f"{_fmt_pace(b['pace_sec_per_km'])} | {mv.num(b['z5_pct'])}"
                    )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_training_block failed")
        return f"Could not read the training block: {exc}"


async def _plan_state(session) -> dict:
    """Everything get_plan_status shows, keyed by mr.plan_status's argument names.

    Indirection so tests can substitute the whole bundle, and so the lazy
    `main` import stays in one place. Guidance, suggestions and the calibration
    preview are each best-effort: a failure in one must not deny the athlete
    the rest. A dict rather than the earlier 6-tuple, which was already hard to
    read and would have grown to eight.
    """
    import main as _main
    plan = await _main._active_plan(session)
    if plan is None:
        return {"plan": None, "workouts": [], "summary": {}, "guidance": None,
                "suggestions": []}

    plan_row = {
        "id": plan.id, "goal_type": plan.goal_type,
        "target_time_sec": plan.target_time_sec, "weeks": plan.weeks,
        "start_date": plan.start_date, "goal_date": plan.goal_date,
    }
    workouts = await _main._workout_dicts(session, plan)
    acts = await _main._plan_activity_dicts(session)
    graded = _main.padh.match_and_grade(workouts, acts, datetime.utcnow(),
                                        plan.start_date)

    guidance = None
    guidance_error = None
    try:
        guidance = await _main.today_guidance(refresh=False, session=session)
    except Exception as exc:  # noqa: BLE001
        logger.exception("today_guidance failed inside get_plan_status")
        guidance_error = (str(exc) or exc.__class__.__name__)[:200]

    suggestions = []
    try:
        suggestions = await _main._plan_suggestions(session, plan)
    except Exception:  # noqa: BLE001
        logger.exception("plan suggestions failed inside get_plan_status")

    # _calibration(session, plan), the helper behind GET /api/plan/calibration:
    # it takes the plan already in hand, where the endpoint would fetch it again.
    # Read-only; the endpoint's extra `history` key is not needed here.
    calibration = None
    calibration_error = None
    try:
        calibration = await _main._calibration(session, plan)
    except Exception as exc:  # noqa: BLE001
        logger.exception("calibration preview failed inside get_plan_status")
        calibration_error = (str(exc) or exc.__class__.__name__)[:200]

    return {"plan": plan_row, "workouts": graded["workouts"],
            "summary": graded["summary"], "guidance": guidance,
            "suggestions": suggestions, "guidance_error": guidance_error,
            "calibration": calibration, "calibration_error": calibration_error}


@mcp.tool()
async def get_plan_status() -> str:
    """Where the training plan is, what today holds, and what should change.

    Start here for any question about the plan rather than a specific run:
    "what am I doing today?", "am I keeping up?", "should I move tomorrow's
    session?". One call returns the plan and its target, the current week's
    sessions with their ids and whether each is on the watch, how many sessions
    have been completed, today's guidance given readiness and heat, and any
    outstanding suggestions the server has derived from measured adherence,
    and, when the athlete's easy runs support it, a pending pace calibration
    (what would change and why; nothing is applied by reading it).

    Each session is listed with its id in brackets. Those ids are stable and
    are what a future write tool will take; there is no write tool for plans
    in this connector yet, so report them to the athlete rather than offering
    to act on them.
    """
    try:
        async with async_session() as session:
            state = await _plan_state(session)
            return mr.plan_status(today=datetime.utcnow(), **state)
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_plan_status failed")
        return f"Could not read the plan: {exc}"


async def _set_workout_note(session, workout_id: int, note: str):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return await _main.set_planned_workout_note(
        workout_id, _main.NoteRequest(note=note), session)


@mcp.tool()
async def set_workout_note(workout_id: int, note: str) -> str:
    """Replace a planned session's description with the athlete's own text.

    The description is the athlete's own instruction to himself for that
    session. The generator's default easy-run text recommends walk breaks, and
    this athlete must not take them (start-stop running causes next-day pain),
    so correcting that text is the main use. Write only what the athlete has
    told you; do not invent one. Replaces any existing description; an empty
    string clears it.

    This changes the text only. It does NOT change paces, distances, dates or
    anything on the watch.

    Args:
        workout_id: the planned workout id, from get_plan_status.
        note: the new description (max 2000 characters). Empty clears it.
    """
    try:
        async with async_session() as session:
            try:
                result = await _set_workout_note(session, workout_id, note)
            except HTTPException as exc:
                return mv.cap_text(
                    f"Nothing changed on workout {workout_id}: {exc.detail}.")
        saved = (result or {}).get("description")
        if not saved:
            return mv.cap_text(f"Description cleared on workout {workout_id}.")
        return mv.cap_text(f"Description saved on workout {workout_id}: {saved}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("set_workout_note failed")
        return f"Could not save the note on workout {workout_id}: {exc}"


def _watch_clause(garmin_workout_id, eligible: bool, reason: str = "",
                  push_ok: Optional[bool] = None) -> str:
    """One sentence on whether the watch holds this session. Pure; no session.

    eligible: the session belongs on the watch (future, has steps, not rest).
    push_ok:  the endpoint's own verdict on the re-push it just attempted
              (True/False), or None when no push was attempted.

    An eligible session whose push failed is a FAILURE even when a
    garmin_workout_id is still set: the endpoint leaves the previous id in
    place, and that id points at the old version on the old day.
    """
    if eligible and push_ok is False:
        return ("The watch was NOT updated: the Garmin push failed, so the "
                "watch still holds the old version of this session.")
    if eligible and push_ok is True:
        return "The watch has been updated and holds this session."
    if garmin_workout_id:
        return "This session is on the watch."
    if eligible:
        return ("The watch was NOT updated: this session belongs on the watch "
                "but is not there, so the watch does not match the plan.")
    why = f" ({reason})" if reason else ""
    return f"It is not on the watch and does not need to be{why}."


async def _move_workout(session, workout_id: int, date: str):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    result = await _main.move_workout(
        workout_id, _main.WorkoutMoveRequest(date=date), session)
    # The response is the whole plan; re-read the moved row for its true state.
    from models import PlannedWorkout
    session.expire_all()
    w = await session.get(PlannedWorkout, workout_id)
    return result, w


@mcp.tool()
async def move_workout(workout_id: int, date: str) -> str:
    """Move one planned session to a different date.

    The plan is the source of truth and the watch follows it: a move to a
    future date re-pushes the session to the Garmin watch, and a move into the
    past takes it off. The reply says whether the watch actually took the
    change. If the re-push failed, the move is saved but the watch still holds
    the old day, and the reply says so plainly; tell the athlete that, and
    offer to retry with push_workout. Also warns (without blocking) when the
    move puts two hard sessions within a day of each other.

    Args:
        workout_id: the planned workout id, from get_plan_status.
        date: the new date, YYYY-MM-DD.
    """
    try:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return mv.cap_text(
                f"Nothing changed: '{date}' is not a valid date. Use YYYY-MM-DD.")
        async with async_session() as session:
            try:
                result, w = await _move_workout(session, workout_id, date)
            except HTTPException as exc:
                return mv.cap_text(
                    f"Nothing changed on workout {workout_id}: {exc.detail}.")
            new_date = w.date.date()
            has_steps = bool((w.structure or {}).get("steps"))
            is_future = new_date >= datetime.utcnow().date()
            rest = w.day_type == "rest"
            eligible = is_future and has_steps and not rest
            if not is_future:
                reason = "it is in the past"
            elif rest:
                reason = "it is a rest day"
            else:
                reason = "it has no structured steps"
            clause = _watch_clause(w.garmin_workout_id, eligible, reason,
                                   push_ok=(result or {}).get("garmin_synced")
                                   if eligible else None)
            lines = [f"Moved \"{w.title}\" (workout {workout_id}) to "
                     f"{new_date.isoformat()}.", clause]
            warning = (result or {}).get("warning")
            if warning:
                lines.append(warning)
            return mv.cap_text(" ".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("move_workout failed")
        return f"Could not move workout {workout_id}: {exc}"


async def _push_workout(session, workout_id: int):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return await _main.push_workout_to_garmin(workout_id, session)


@mcp.tool()
async def push_workout(workout_id: int) -> str:
    """Schedule one planned session on the Garmin watch, so it guides the run
    step by step.

    Two failures read differently on purpose. A session with no structured
    steps can never be sent, so asking again will not help. A Garmin outage is
    temporary, so trying again later is worthwhile.

    Args:
        workout_id: the planned workout id, from get_plan_status.
    """
    try:
        async with async_session() as session:
            try:
                result = await _push_workout(session, workout_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return mv.cap_text(
                        f"Nothing sent: workout {workout_id} was not found.")
                if exc.status_code == 400:
                    return mv.cap_text(
                        f"Nothing was sent to the watch for workout {workout_id}: "
                        f"{exc.detail} Asking again will not help; the session "
                        f"would need steps added before it could go on the watch.")
                return mv.cap_text(
                    f"The session has not been sent to the watch (workout "
                    f"{workout_id}): {exc.detail}. Garmin may be unavailable "
                    f"right now; try again shortly.")
            result = result or {}
            clause = _watch_clause(result.get("garmin_workout_id"), True,
                                   push_ok=True)
            return mv.cap_text(
                f"Pushed \"{result.get('title')}\" (workout {workout_id}) for "
                f"{result.get('date')}. {clause}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("push_workout failed")
        return f"Could not push workout {workout_id}: {exc}"


async def _unpush_workout(session, workout_id: int):
    """Indirection so tests can substitute the import. See _garmin_sync.

    The endpoint returns {"garmin_workout_id": None} both after removing a
    session and when there was nothing to remove, so the only way to tell
    which happened is to read the id BEFORE calling. Returns
    (title, was_on_watch, result); title is None for an unknown workout, and
    the endpoint then raises its own 404.
    """
    import main as _main
    from models import PlannedWorkout
    w = await session.get(PlannedWorkout, workout_id)
    title = w.title if w is not None else None
    was_on_watch = bool(w is not None and w.garmin_workout_id)
    result = await _main.remove_workout_from_garmin(workout_id, session)
    return title, was_on_watch, result


@mcp.tool()
async def unpush_workout(workout_id: int) -> str:
    """Take one planned session off the Garmin watch. The plan is untouched:
    the session stays in the plan and can be pushed again later with
    push_workout. This connector does not delete planned sessions.

    The reply says whether the session was actually taken off the watch or was
    never on it, and if Garmin refused, that it is still on the watch.

    Args:
        workout_id: the planned workout id, from get_plan_status.
    """
    try:
        async with async_session() as session:
            try:
                title, was_on_watch, _ = await _unpush_workout(session, workout_id)
            except HTTPException as exc:
                if exc.status_code == 404:
                    return mv.cap_text(
                        f"Nothing changed: workout {workout_id} was not found.")
                return mv.cap_text(
                    f"Nothing changed on workout {workout_id}: {exc.detail}. "
                    f"The session is still on the watch; try again shortly.")
            if was_on_watch:
                return mv.cap_text(
                    f"\"{title}\" (workout {workout_id}) has been taken off the "
                    f"watch. It is still in the plan.")
            return mv.cap_text(
                f"\"{title}\" (workout {workout_id}) was not on the watch, so "
                f"there was nothing to take off. It is still in the plan.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("unpush_workout failed")
        return f"Could not take workout {workout_id} off the watch: {exc}"


# Upper bound on failed sessions listed by sync_plan_to_watch. A 12-week plan
# has ~41 sessions; the rest are summarised as "and N more" so the response
# fits by construction instead of leaning on mv.cap_text.
MAX_SYNC_FAILURES_LISTED = 15
_SYNC_TITLE_CHARS = 80


async def _sync_plan_to_watch(session):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return await _main.sync_plan_to_watch(session)


@mcp.tool()
async def sync_plan_to_watch() -> str:
    """Push the whole active plan to the Garmin watch. Use it after a plan is
    created or recalibrated.

    It reports which sessions failed and why, by name, so a partial sync is
    visible rather than hidden behind a count. Only upcoming, structured,
    non-rest sessions are sent; past sessions and sessions without steps are
    skipped, which is not a failure. (This is not sync_garmin, which pulls
    finished runs in from Garmin.)
    """
    skipped_note = ("Only upcoming structured sessions are sent; rest days, "
                    "sessions without steps and past dates are skipped.")
    try:
        async with async_session() as session:
            try:
                result = await _sync_plan_to_watch(session)
            except HTTPException as exc:
                return mv.cap_text(f"Nothing was sent to the watch: {exc.detail}.")
            result = result or {}
            pushed = int(result.get("pushed") or 0)
            failed = result.get("failed") or []
            total_failed = int(result.get("total_failed", len(failed)))

            if not total_failed:
                if not pushed:
                    return mv.cap_text(
                        f"Nothing was sent: the active plan has no upcoming "
                        f"structured sessions. {skipped_note}")
                return mv.cap_text(
                    f"Sent {pushed} session{'s' if pushed != 1 else ''} to the "
                    f"watch. No failures. {skipped_note}")

            lines = [f"Sent {pushed} session{'s' if pushed != 1 else ''} to the "
                     f"watch; {total_failed} failed. These have not been sent "
                     f"to the watch, so it will not guide those runs:"]
            for f in failed[:MAX_SYNC_FAILURES_LISTED]:
                title = str(f.get("title") or "Untitled")[:_SYNC_TITLE_CHARS]
                lines.append(f"- {title} (workout {f.get('id')}): {f.get('error')}")
            if total_failed > MAX_SYNC_FAILURES_LISTED:
                lines.append(f"  … and {total_failed - MAX_SYNC_FAILURES_LISTED} more")
            lines.append(skipped_note)
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("sync_plan_to_watch failed")
        return f"Could not sync the plan to the watch: {exc}"


async def _push_adhoc(name: str, date: str, steps: list):
    """Indirection so tests can substitute the import. See _garmin_sync.

    The endpoint takes no database session (main.py: push_adhoc_workout(req)).
    """
    import main as _main
    return await _main.push_adhoc_workout(
        _main.AdhocWorkoutRequest(name=name, date=date, steps=steps))


def _pace_step_problem(steps: list, where: str = "step") -> Optional[str]:
    """First problem with a pace target that garmin_workout would silently
    mangle: a missing bound drops the target entirely, and swapped bounds give
    the watch an inverted range. Returns a sentence, or None when fine."""
    for i, s in enumerate(steps, 1):
        label = f"{where} {i}"
        if not isinstance(s, dict):
            return f"{label} is not an object."
        if s.get("type") == "repeat":
            found = _pace_step_problem(s.get("steps") or [], f"{label}, inner step")
            if found:
                return found
            continue
        if s.get("target_kind") == "pace":
            low, high = s.get("pace_low_sec"), s.get("pace_high_sec")
            if not low or not high:
                return (f"{label} has target_kind \"pace\" but needs both "
                        f"pace_low_sec and pace_high_sec, otherwise the watch "
                        f"would get no pace target at all.")
            if low > high:
                return (f"{label} has pace_low_sec ({low}) slower than "
                        f"pace_high_sec ({high}). pace_low_sec is the FASTER "
                        f"bound, so it must be the smaller number.")
    return None


@mcp.tool()
async def push_adhoc_workout(name: str, date: str, steps: list[dict]) -> str:
    """Send a one-off structured session to the Garmin watch with no plan behind
    it: a benchmark attempt, a test run. The watch then guides it step by step
    and can hold the athlete to a pace. It cannot be a past date.

    UNITS: every pace is SECONDS PER KILOMETRE (8:00/km = 480). end_value is
    SECONDS when end_kind is "time" and METRES when it is "distance".

    `steps` is an ordered list of step objects. A normal step has these keys:
      type         "warmup" | "run" | "recovery" | "cooldown"  ("run" is the
                   hard/working step; an unrecognised type is treated as "run")
      end_kind     "time" | "distance"  (anything else is treated as "time")
      end_value    number: seconds for "time", metres for "distance"
      target_kind  "pace" | "none"  (use "none" for no pace guard)
      pace_low_sec   the FASTER bound, the smaller number (e.g. 470)
      pace_high_sec  the SLOWER bound, the larger number (e.g. 490)
      note         optional text shown on the watch for that step (512 chars)
    With target_kind "pace" BOTH pace bounds are required, and pace_low_sec must
    be smaller than pace_high_sec; this tool refuses a step that breaks that,
    because the watch would otherwise get no target or an inverted one. For
    target_kind "none" the pace keys are ignored.

    To repeat a block, use one repeat step holding the steps to loop:
      {"type": "repeat", "iterations": N, "steps": [ ...normal steps... ]}
    Repeats cannot be nested; a repeat inside a repeat is not supported.

    Worked example: 10 min easy warmup, 3 x (800 m hard at 4:30-4:40/km, then
    90 s easy jog), 10 min cooldown:
    [
      {"type": "warmup", "end_kind": "time", "end_value": 600,
       "target_kind": "none"},
      {"type": "repeat", "iterations": 3, "steps": [
        {"type": "run", "end_kind": "distance", "end_value": 800,
         "target_kind": "pace", "pace_low_sec": 270, "pace_high_sec": 280,
         "note": "Controlled, not a sprint."},
        {"type": "recovery", "end_kind": "time", "end_value": 90,
         "target_kind": "none"}
      ]},
      {"type": "cooldown", "end_kind": "time", "end_value": 600,
       "target_kind": "none"}
    ]

    Returns the Garmin workout id. The session lives only on Garmin, not in the
    training plan; a failed push means nothing has been sent to the watch.

    Args:
        name: what the watch shows for the session, e.g. "5K benchmark".
        date: YYYY-MM-DD, today or later.
        steps: the step list described above.
    """
    try:
        try:
            when = datetime.fromisoformat(str(date)).date()
        except ValueError:
            return mv.cap_text(
                f"The session has not been sent to the watch: date must be "
                f"YYYY-MM-DD, got {date!r}.")
        if when < datetime.utcnow().date():
            return mv.cap_text(
                f"The session has not been sent to the watch: {when.isoformat()} "
                f"is in the past, and Garmin would accept it but the watch "
                f"would never show it. Pick today or a later date.")
        if not steps:
            return mv.cap_text(
                "The session has not been sent to the watch: it needs at least "
                "one step. See this tool's description for the step format.")
        problem = _pace_step_problem(steps)
        if problem:
            return mv.cap_text(
                f"The session has not been sent to the watch: {problem}")
        try:
            res = await _push_adhoc(name, when.isoformat(), steps)
        except HTTPException as exc:
            return mv.cap_text(
                f"The session has not been sent to the watch: {exc.detail}"
                + (" Garmin may be unavailable; try again shortly."
                   if exc.status_code >= 500 else ""))
        res = res or {}
        return mv.cap_text(
            f"Sent \"{res.get('name', name)}\" to the watch for "
            f"{res.get('date', when.isoformat())} "
            f"({res.get('steps', len(steps))} steps). Garmin workout id: "
            f"{res.get('workout_id')}. This is a one-off, not part of the plan.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("push_adhoc_workout failed")
        return f"Could not push the workout: {exc}"


async def _list_suggestions(session):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return (await _main.get_plan_suggestions(session))["suggestions"]


async def _apply_suggestion(session, suggestion_id: str):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return await _main.apply_plan_suggestion(
        _main.SuggestionApplyRequest(id=suggestion_id), session)


def _bulleted(entries: list, line) -> list:
    """At most MAX_SYNC_FAILURES_LISTED bullets, then '… and N more'."""
    out = [line(e) for e in entries[:MAX_SYNC_FAILURES_LISTED]]
    if len(entries) > MAX_SYNC_FAILURES_LISTED:
        out.append(f"  … and {len(entries) - MAX_SYNC_FAILURES_LISTED} more")
    return out


def _session_ref(e: dict) -> str:
    title = str(e.get("title") or "Untitled")[:_SYNC_TITLE_CHARS]
    was = str(e.get("was_titled") or title)[:_SYNC_TITLE_CHARS]
    named = f"\"{title}\"" + (f" (the watch has it as \"{was}\")" if was != title else "")
    return f"- {named} on {e.get('date')} (workout {e.get('id')})"


def _suggestion_watch_lines(watch: dict) -> list:
    """What the suggestion did to the watch, one block per outcome. Pure."""
    unscheduled = watch.get("unscheduled") or []
    repushed = watch.get("repushed") or []
    failed = watch.get("failed") or []
    lines = []
    if unscheduled:
        lines.append(
            "These sessions came off the watch and are NOT on the watch now. "
            "What the watch runs (the steps) no longer matches the changed "
            "session, and sending the old steps would give you the wrong "
            "workout. They are still in the plan: run them as the plan now "
            "describes. Do not use push_workout on them until their steps "
            "have been regenerated:")
        lines += _bulleted(unscheduled, _session_ref)
    if repushed:
        lines.append("Re-sent to the watch with the new details:")
        lines += _bulleted(repushed, _session_ref)
    removal = [f for f in failed if f.get("action") == "remove"]
    update = [f for f in failed if f.get("action") != "remove"]
    if removal:
        lines.append(
            "The watch was NOT changed for these: they are still on the watch "
            "holding the OLD version of the session, which no longer matches "
            "the plan. Do not run them from the watch. Retry with "
            "unpush_workout:")
        lines += _bulleted(removal, lambda f: f"{_session_ref(f)}: {f.get('error')}")
    if update:
        lines.append(
            "The watch update failed for these: the watch still holds the OLD "
            "version. Retry with push_workout:")
        lines += _bulleted(update, lambda f: f"{_session_ref(f)}: {f.get('error')}")
    if not lines:
        lines.append("Nothing on the watch needed changing.")
    return lines


@mcp.tool()
async def apply_suggestion(suggestion_id: str) -> str:
    """Apply one of the app's own plan suggestions, such as capping a long run
    after a missed one or swapping a speed session for an easy run.

    The ids come from get_plan_status. The server re-derives the suggestion list
    and refuses an id that no longer applies, so a stale id is safe: it changes
    nothing.

    Applying a suggestion changes the plan. A session that was on the watch is
    handled with care: if the change makes the watch's step list wrong (a new
    distance, pace or session type) the session is taken OFF the watch and named
    in the reply, because re-sending the old steps would give the athlete the
    wrong workout; only cosmetic changes are re-sent. Nothing is ever newly
    scheduled on the watch.

    Args:
        suggestion_id: the id of the suggestion, from get_plan_status.
    """
    try:
        async with async_session() as session:
            title = detail = None
            try:
                for s in await _list_suggestions(session):
                    if s.get("id") == suggestion_id:
                        title, detail = s.get("title"), s.get("detail")
            except Exception:  # noqa: BLE001 — the title is nice-to-have
                logger.exception("apply_suggestion: could not read suggestion text")
            try:
                resp = await _apply_suggestion(session, suggestion_id)
            except HTTPException as exc:
                return mv.cap_text(
                    f"The suggestion was not applied: {exc.detail}. "
                    f"Nothing was changed.")
            applied = (f"Applied: {title}. {detail or ''}".strip()
                       if title else f"Applied suggestion {suggestion_id}.")
            lines = [applied, "The plan is updated."]
            lines += _suggestion_watch_lines((resp or {}).get("watch") or {})
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply_suggestion failed")
        return f"Could not apply the suggestion: {exc}"


async def _apply_calibration(session):
    """Indirection so tests can substitute the import. See _garmin_sync."""
    import main as _main
    return await _main.apply_plan_calibration(session)


@mcp.tool()
async def apply_calibration() -> str:
    """Accept the pace calibration previewed in get_plan_status: re-aim the
    plan's remaining easy, long and strides sessions at the easy pace measured
    from recent runs, and re-send the affected sessions to the watch so the
    plan and the watch agree.

    Takes no arguments; look at the preview in get_plan_status first. It only
    touches sessions from today onward (the past is history and is never
    rewritten). Quality and race sessions sit on goal pace and are not moved.
    Sessions that are not on the watch are updated in the plan only. If some
    re-sends fail, the plan keeps the new targets and the reply names the
    sessions whose watch copy still holds the OLD targets.
    """
    try:
        async with async_session() as session:
            try:
                resp = await _apply_calibration(session)
            except HTTPException as exc:
                return mv.cap_text(
                    f"Calibration was not applied: {str(exc.detail).rstrip('.')}. "
                    f"Nothing was changed.")
            cal = (resp or {}).get("calibration") or {}
            updated = int(cal.get("workouts_updated") or 0)
            repushed = int(cal.get("repushed_to_watch") or 0)
            failed = cal.get("failed") or []
            changes = cal.get("changes") or []

            lines = [("Calibration saved, but "
                      f"{len(failed)} session{'s' if len(failed) != 1 else ''} "
                      "did not reach the watch." if failed
                      else "Calibration applied.")]
            for c in changes[:MAX_SYNC_FAILURES_LISTED]:
                lines.append(f"  {c.get('field')}: {c.get('from')} -> {c.get('to')}")
                if c.get("reason"):
                    lines.append(f"    {c['reason']}")
            lines.append(
                f"Re-aimed {updated} upcoming session{'s' if updated != 1 else ''} "
                f"at the new easy pace; {repushed} of them "
                f"{'was' if repushed == 1 else 'were'} re-sent to the watch with "
                "the new targets. Past sessions were left alone.")
            unsent = updated - repushed - len(failed)
            if unsent > 0:
                one = unsent == 1
                lines.append(
                    f"{unsent} of the re-aimed session{'' if one else 's'} "
                    f"{'was' if one else 'were'} not on the watch (or had no "
                    f"steps), so only the plan changed for "
                    f"{'it' if one else 'them'}.")
            if failed:
                lines.append(
                    "The plan has the new targets for these, but they have not "
                    "been sent to the watch, which still holds the OLD pace "
                    "targets. Retry with push_workout:")
                lines += _bulleted(failed, lambda f: (
                    f"- {str(f.get('title') or 'Untitled')[:_SYNC_TITLE_CHARS]}"
                    f" (workout {f.get('id')})"))
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply_calibration failed")
        return f"Could not apply the calibration: {exc}"
