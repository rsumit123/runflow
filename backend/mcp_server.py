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
from datetime import datetime
from typing import Optional

from sqlalchemy import select

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

            lines = ["id | date | dist | pace | avgHR | maxHR | Z5% | TE | heat | notes"]
            for a in acts:
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if (km and a.moving_time) else None
                shares = mv.zone_shares(a.hr_zones)
                st = await _streams_for(session, a.id)
                pauses = mv.find_pauses(st.get("time"), st.get("distance"))
                notes = f"fragmented ({len(pauses)} pauses)" if pauses else ""
                lines.append(
                    f"{a.id} | {a.start_date:%Y-%m-%d} | {km:.2f}km | "
                    f"{_fmt_pace(pace)} | {a.average_heartrate or '—'} | "
                    f"{a.max_heartrate or '—'} | {shares.get(5, '—')} | "
                    f"{a.aerobic_te or '—'} {a.training_effect_label or ''} | "
                    f"{a.heat_penalty_sec or '—'}s/km | {notes}"
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
                f"HR avg {a.average_heartrate or '—'} / max {a.max_heartrate or '—'}"
                f" · cadence {cad}",
                f"Training Effect {a.aerobic_te or '—'} aerobic /"
                f" {a.anaerobic_te or '—'} anaerobic {a.training_effect_label or ''}",
            ]

            shares = mv.zone_shares(a.hr_zones)
            if shares:
                out.append("Zones: " + "  ".join(
                    f"Z{z} {shares[z]}%" for z in sorted(shares)
                ))

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
                        f"HR {s['hr_avg'] or '—'} (peak {s['hr_peak'] or '—'})"
                    )

            pauses = mv.find_pauses(time, dist, hr)
            if pauses:
                out += ["", f"Stopped {len(pauses)} time(s) — this was not a "
                            "continuous run:"]
                for p in pauses:
                    out.append(
                        f"  at {p['at_km']:.2f} km — paused {_fmt_dur(p['seconds'])}"
                        f" (HR {p['hr_in'] or '—'} in, {p['hr_out'] or '—'} out)"
                    )

            rd = a.running_dynamics or {}
            if rd:
                out += ["", (
                    f"Dynamics: stride {rd.get('stride_length')} cm · "
                    f"ground contact {rd.get('ground_contact_time')} ms · "
                    f"vertical oscillation {rd.get('vertical_oscillation')} cm"
                )]

            if a.dew_point_c is not None:
                out += ["", (
                    f"Conditions: {a.temp_c}°C, dew point {a.dew_point_c}°C"
                    f" (index {a.heat_index}) — cost ~{a.heat_penalty_sec} s/km;"
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
                    f"{_fmt_pace(pace)} | {a.average_heartrate or '—'} | "
                    f"{shares.get(5, '—')} | "
                    f"{f'{drift:+.1f}' if drift is not None else '—'} | "
                    f"{a.aerobic_te or '—'} | {a.dew_point_c or '—'}°C | "
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

    The current phases, the active plan and its week number, today's planned
    workout with the readiness-based recommendation, and progress toward the
    sub-6:00/km gate — reported both as the raw best pace and as the
    weather-normalized equivalent, because dew point has been costing 20+ s/km.
    """
    try:
        import main as _main
        async with async_session() as session:
            # gap_days must be passed explicitly: its endpoint default is a
            # FastAPI Query object, which is only resolved by the HTTP layer.
            phases = await _main.get_phases(gap_days=14, session=session)
            plan = await _main.get_active_plan(session)
            try:
                guidance = await _main.today_guidance(session=session)
            except Exception as exc:  # noqa: BLE001 — needs the watch, may be absent
                guidance = {"note": f"today's guidance unavailable: {exc}"}

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
            gate = (
                f"Gate (sub-6:00/km): best of last {len(acts)} runs is "
                f"{_fmt_pace(best_raw)} raw, {_fmt_pace(best_norm)} "
                "weather-normalized."
            )
            return mv.cap_text(
                "Phases:\n" + _json_block(phases)
                + "\n\nActive plan:\n" + _json_block(plan)
                + "\n\nToday:\n" + _json_block(guidance)
                + "\n\n" + gate
            )
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

    PREFER THE SPECIFIC TOOLS. list_recent_runs, get_run_detail, compare_runs,
    get_recovery, get_records and get_training_context return analysed summaries
    — splits on moving time, cardiac drift, zone shares, pauses,
    weather-normalized pace — that this tool does not compute. Use call_api only
    when none of them covers the question, for example a route-level breakdown
    or monthly stats.

    GET routes only, so nothing here can change data. Encoded GPS polylines and
    raw sample streams are removed unless include_heavy is true, and streams are
    then thinned to at most 200 points (the true sample count is reported).
    Output is capped at 25,000 characters.

    Args:
        path: the route path including the /api prefix, e.g. "/api/stats/monthly".
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
