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
