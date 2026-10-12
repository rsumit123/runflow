"""Report composition for the MCP connector.

mcp_views.py holds the primitives — one computation each, no formatting
decisions. This module holds the opposite: no computation, only the decisions
about what belongs in one answer and in what order. It takes already-fetched
rows and streams and returns text, so it needs no database and no network, and
mcp_server.py is left with nothing but tool declaration and row fetching.
"""
from __future__ import annotations

from typing import Any, Optional

import mcp_views as mv


def _pace(sec_per_km: Optional[float]) -> str:
    if not sec_per_km:
        return "—"
    m, s = divmod(round(sec_per_km), 60)
    return f"{m}:{s:02d}"


def _dur(secs: Optional[float]) -> str:
    if not secs:
        return "—"
    m, s = divmod(round(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def run_review(run: dict[str, Any], streams: dict[str, list],
                planned: Optional[dict[str, Any]] = None,
                window_sec: int = 60) -> str:
    """One run, in the depth a coaching conversation needs.

    Order matters here: the headline numbers first, then whether the run was
    continuous — which for an athlete who cannot tolerate stop-start running is
    more important than the pace — then splits, then how HR behaved, then the
    context the sensors cannot supply, and last how it compared to what was
    planned.
    """
    km = (run.get("distance") or 0.0) / 1000.0
    moving = run.get("moving_time") or 0
    elapsed = run.get("elapsed_time") or moving
    lines: list[str] = []

    date = run.get("start_date")
    lines.append(f"Run {run.get('id')} — {run.get('name') or 'Run'}"
                 + (f" — {date:%Y-%m-%d %H:%M}" if date else ""))
    lines.append(
        f"{km:.1f} km in {_dur(moving)} at {_pace(moving / km if km else None)}/km"
        f" · avg HR {mv.num(run.get('average_heartrate'))}"
        f" · max HR {mv.num(run.get('max_heartrate'))}"
        f" · cadence {mv.num(run.get('average_cadence'))}"
    )

    mpb = mv.metres_per_beat(run.get("distance"), moving, run.get("average_heartrate"))
    if mpb:
        lines.append(f"Aerobic efficiency: {mv.num(mpb, 2)} metres per heartbeat")

    terr = mv.terrain(run.get("total_elevation_gain"), run.get("elev_high"),
                      run.get("elev_low"), run.get("distance"))
    if terr:
        lines.append(f"Terrain: {terr['label']} "
                     f"({mv.num(terr['gain_m'])} m gain, {mv.num(terr['range_m'])} m range)")
    if run.get("temp_c") is not None:
        lines.append(f"Conditions: {mv.num(run.get('temp_c'))}°C, "
                     f"dew point {mv.num(run.get('dew_point_c'))}°C")

    time, dist, hr = streams.get("time"), streams.get("distance"), streams.get("heartrate")
    if not time or not dist:
        lines.append("")
        lines.append("No stream data stored for this run — no splits or HR profile.")
    else:
        pauses = mv.find_pauses(time, dist, hr)
        lines.append("")
        if pauses:
            lines.append(f"FRAGMENTED — {len(pauses)} pause(s), "
                         f"{_dur(elapsed - moving)} stopped in total:")
            for p in pauses:
                lines.append(f"  at {mv.num(p['at_km'], 2)} km — {_dur(p['seconds'])}, "
                             f"HR {mv.num(p['hr_in'])} → {mv.num(p['hr_out'])}")
        else:
            lines.append(f"CONTINUOUS — {elapsed - moving} s stopped across the whole run.")

        splits = mv.split_table(dist, time, hr, metres=500)
        if splits:
            lines.append("")
            lines.append("Splits (500 m, moving time):")
            lines.append("  from-to | pace | avg HR | peak HR")
            for s in splits:
                lines.append(f"  {s['from_m']}-{s['to_m']} | {_pace(s['pace_sec_per_km'])} | "
                             f"{mv.num(s['hr_avg'])} | {mv.num(s['hr_peak'])}")

        # Keyword arguments deliberately: hr_profile takes (hr, time, dist)
        # while split_table above takes (dist, time, hr). All three are
        # Optional lists, so a positional transposition would fail silently.
        prof = mv.hr_profile(hr=hr, time=time, dist=dist, window_sec=window_sec)
        if prof:
            lines.append("")
            # "moving time" is not decoration: on a run with pauses the first
            # five minutes of moving time is not the first five on the watch.
            lines.append(f"HR profile ({window_sec} s windows, moving time):")
            lines.append("  window | secs | pace | avg HR | peak HR")
            for p in prof:
                lines.append(f"  {p['from_sec']}-{p['to_sec']}s | {p['seconds']} | "
                             f"{_pace(p['pace_sec_per_km'])} | "
                             f"{mv.num(p['hr_avg'])} | {mv.num(p['hr_peak'])}")

        drift = mv.cardiac_drift(hr)
        if drift is not None:
            lines.append("")
            lines.append(f"Cardiac drift: {mv.num(drift)} bpm "
                         "(second half mean minus first half mean)")

    zones = run.get("hr_zones")
    shares = mv.zone_shares(zones)
    if shares:
        lines.append("")
        lines.append("Time in zones: " + "  ".join(
            f"Z{z} {mv.num(pct)}%" for z, pct in sorted(shares.items())))
        legend = mv.zone_legend(zones)
        if legend:
            lines.append(legend)

    if run.get("notes"):
        lines.append("")
        lines.append(f"Athlete's note: {run['notes']}")

    lines.append("")
    if planned is None:
        lines.append("No planned session matched this run.")
    else:
        lines.append(f"Planned: {planned.get('title') or planned.get('day_type')} "
                     f"({planned.get('day_type')})")
        tgt_km = (planned.get("target_distance_m") or 0) / 1000.0
        lines.append(f"  target {mv.num(tgt_km)} km at "
                     f"{_pace(planned.get('pace_low_sec'))}-"
                     f"{_pace(planned.get('pace_high_sec'))}/km"
                     + (f", HR ceiling {planned['hr_ceiling']}"
                        if planned.get("hr_ceiling") else ""))
        if planned.get("status"):
            lines.append(f"  status: {planned['status']}")
        if planned.get("compliance"):
            # on_target | hr_drift | ran_hard, from plan_adherence._grade_easy.
            lines.append(f"  compliance: {planned['compliance']}")

    return mv.cap_text("\n".join(lines))
