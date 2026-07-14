"""
Fitness model + gray-zone classification + advisory guardrails (Training v1).

Pure computation over a runner's activity history. No DB / no I/O — callers pass
a list of activity dicts and a reference `now`, so this is fully unit-testable.

Each activity dict needs: id, name, distance (m), start_date (datetime),
average_speed (m/s), average_heartrate (float|None), max_heartrate (float|None).

Design notes tied to this runner's reality:
- Only ~46% of runs have HR (older runs pre-date the HR watch). So classification
  is HYBRID: HR-based when present (precise), pace-based otherwise (inferred).
- The runner has essentially no easy-pace history (never trained easy), so
  easy pace is ESTIMATED from the HR/pace relationship and clearly labelled.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

# Classification thresholds as a fraction of the athlete's max HR.
EASY_FRAC = 0.75      # below this = genuinely easy (Zone 1-2)
HARD_FRAC = 0.88      # above this = hard (threshold+)
DEFAULT_MAX_HR = 190  # fallback when no HR data exists at all
EASY_CEILING_FRAC = 0.76  # easy-run HR ceiling as a fraction of max HR


def _pace_sec_per_km(speed_mps: Optional[float]) -> Optional[float]:
    if not speed_mps or speed_mps <= 0:
        return None
    return 1000.0 / speed_mps


def _athlete_max_hr(acts: list[dict[str, Any]]) -> int:
    maxes = [a["max_heartrate"] for a in acts if a.get("max_heartrate")]
    return int(max(maxes)) if maxes else DEFAULT_MAX_HR


def classify_run(
    avg_hr: Optional[float],
    avg_speed: Optional[float],
    athlete_max_hr: int,
    easy_pace_sec: Optional[float],
) -> dict[str, Any]:
    """Return {zone: easy|gray|hard, basis: hr|pace|unknown}."""
    if avg_hr:
        frac = avg_hr / athlete_max_hr if athlete_max_hr else 1.0
        if frac < EASY_FRAC:
            zone = "easy"
        elif frac > HARD_FRAC:
            zone = "hard"
        else:
            zone = "gray"
        return {"zone": zone, "basis": "hr"}

    # No HR — infer from pace vs the estimated easy band.
    pace = _pace_sec_per_km(avg_speed)
    if pace is None or easy_pace_sec is None:
        return {"zone": "unknown", "basis": "unknown"}
    # Slower than (or at) easy pace => easy; faster => gray (can't tell gray/hard
    # apart without HR, so we conservatively call it gray, not hard).
    zone = "easy" if pace >= easy_pace_sec - 10 else "gray"
    return {"zone": zone, "basis": "pace"}


EASY_MEASURE_DAYS = 365   # a season, not a career — beyond this is a different athlete
MIN_EASY_RUNS = 3         # below this it's an anecdote, not a measurement
FRESH_EASY_DAYS = 42      # newer than this and the measurement stands on its own
MAX_DETRAINING = 1.35     # cap: even a long layoff shouldn't slow easy pace by >35%


def _fitness_decay(acts: list[dict[str, Any]], now: datetime,
                   since: datetime) -> float:
    """How much slower the runner's top end is NOW than it was around `since`.

    Both ends measured on heat-normalised pace, so a monsoon doesn't get logged
    as detraining. Returns a multiplier ≥ 1.0 (1.18 = 18% slower than they were).
    """
    def best(lo: datetime, hi: datetime) -> Optional[float]:
        paces = [
            a.get("normalized_pace_sec") or _pace_sec_per_km(a.get("average_speed"))
            for a in acts
            if (a.get("distance") or 0) >= 1500 and a.get("start_date")
            and lo <= a["start_date"] < hi
            and (a.get("normalized_pace_sec") or a.get("average_speed"))
        ]
        return min(paces) if paces else None

    recent = best(now - timedelta(days=FRESH_EASY_DAYS), now)
    era = best(since - timedelta(days=60), since + timedelta(days=14))
    if not recent or not era or era <= 0:
        return 1.0
    # Only ever penalise. If they've got FASTER since, the easy measurement is
    # already conservative and we leave it alone.
    return max(1.0, min(MAX_DETRAINING, recent / era))


def _estimate_easy_pace(
    acts: list[dict[str, Any]], easy_hr_ceiling: int, threshold_pace_sec: Optional[float],
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Easy pace (sec/km) — MEASURED from runs actually run at an easy heart rate,
    and only estimated when there's nothing to measure.

    Two things matter here:

    1. Prefer evidence. Anchoring easy pace to threshold × 1.30 is a guess, and
       it was producing 7:15/km for a runner whose own easy-HR runs said 6:31.
    2. Compare like with like. A run's raw pace conflates fitness with weather,
       so we measure on the HEAT-NORMALISED pace where we have it. Otherwise a
       cool-season run and a monsoon run get averaged as if they were the same.
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=EASY_MEASURE_DAYS)

    easy = []
    for a in acts:
        hr = a.get("average_heartrate")
        sd = a.get("start_date")
        if not hr or not sd or sd < cutoff or hr > easy_hr_ceiling:
            continue
        if (a.get("distance") or 0) < 1000:
            continue
        # Heat-normalised where we have it; raw pace is the fallback.
        pace = a.get("normalized_pace_sec") or _pace_sec_per_km(a.get("average_speed"))
        if pace:
            easy.append((sd, pace))

    if len(easy) >= MIN_EASY_RUNS:
        easy.sort(key=lambda p: p[0], reverse=True)
        used = easy[:10]
        measured = sum(p for _, p in used) / len(used)
        newest = used[0][0]
        stale_days = (now - newest).days

        # A measurement from before a layoff describes a runner who no longer
        # exists. Scale it by how much top-end fitness has actually moved since
        # then — measured on normalised pace, so we're not just charging them for
        # the weather twice.
        if stale_days > FRESH_EASY_DAYS:
            decay = _fitness_decay(acts, now, newest)
            adjusted = measured * decay
            return {
                "easy_pace_sec": round(adjusted),
                "method": "measured_stale" if decay > 1.0 else "measured",
                "measured_easy_pace_sec": round(measured),
                "staleness_days": stale_days,
                "detraining_factor": round(decay, 3),
                "easy_runs_used": len(used),
                "easy_runs_available": len(easy),
            }

        return {
            "easy_pace_sec": round(measured),
            "method": "measured",
            "easy_runs_used": len(used),
            "easy_runs_available": len(easy),
        }

    if threshold_pace_sec:
        est = threshold_pace_sec * 1.30
        est = max(420.0, min(540.0, est))  # clamp 7:00–9:00 /km
        return {"easy_pace_sec": round(est), "method": "estimate",
                "easy_runs_available": len(easy)}
    return {"easy_pace_sec": None, "method": "insufficient_data"}


def _threshold_pace_sec(acts: list[dict[str, Any]], now: datetime) -> Optional[float]:
    """Fastest sustained average pace over a meaningful distance, recent-weighted.

    Measured on heat-normalised pace: a 5:35 set in February and a 6:37 set in the
    monsoon are not 62 s/km of lost fitness, and reporting them as if they were
    tells a runner they're falling apart when they're mostly just hot.
    """
    def fastest(min_dist: float, since_days: Optional[int]) -> Optional[float]:
        paces = []
        for a in acts:
            if (a.get("distance") or 0) < min_dist:
                continue
            if since_days is not None and a.get("start_date"):
                if a["start_date"] < now - timedelta(days=since_days):
                    continue
            p = a.get("normalized_pace_sec") or _pace_sec_per_km(a.get("average_speed"))
            if p:
                paces.append(p)
        return min(paces) if paces else None

    return (
        fastest(1500, 180)
        or fastest(1000, 365)
        or fastest(800, None)
    )


def _window_km(acts: list[dict[str, Any]], now: datetime, days: int) -> float:
    cutoff = now - timedelta(days=days)
    return sum(
        (a.get("distance") or 0) / 1000.0
        for a in acts
        if a.get("start_date") and a["start_date"] >= cutoff
    )


def _longest_km(acts: list[dict[str, Any]], now: datetime, days: int) -> float:
    cutoff = now - timedelta(days=days)
    dists = [
        (a.get("distance") or 0) / 1000.0
        for a in acts
        if a.get("start_date") and a["start_date"] >= cutoff
    ]
    return round(max(dists), 2) if dists else 0.0


def build_fitness_model(acts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    max_hr = _athlete_max_hr(acts)
    easy_hr_ceiling = round(EASY_CEILING_FRAC * max_hr)

    acute_7d = round(_window_km(acts, now, 7), 1)
    chronic_28d_weekly = round(_window_km(acts, now, 28) / 4.0, 1)
    acwr = round(acute_7d / chronic_28d_weekly, 2) if chronic_28d_weekly > 0 else None

    threshold = _threshold_pace_sec(acts, now)
    easy = _estimate_easy_pace(acts, easy_hr_ceiling, threshold, now)

    return {
        "athlete_max_hr": max_hr,
        "easy_hr_ceiling": easy_hr_ceiling,
        "easy_pace_sec": easy["easy_pace_sec"],
        "easy_pace_method": easy["method"],
        "threshold_pace_sec": round(threshold) if threshold else None,
        "weekly_volume_28d_km": round(_window_km(acts, now, 28) / 4.0, 1),
        "longest_run_28d_km": _longest_km(acts, now, 28),
        "longest_run_90d_km": _longest_km(acts, now, 90),
        "acute_load_7d_km": acute_7d,
        "chronic_load_28d_km": chronic_28d_weekly,
        "acwr": acwr,
        "hr_coverage": sum(1 for a in acts if a.get("average_heartrate")),
        "total_runs": len(acts),
    }


def _recent_sorted(acts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [a for a in acts if a.get("start_date")],
        key=lambda a: a["start_date"],
        reverse=True,
    )


def gray_zone_summary(acts: list[dict[str, Any]], now: datetime, model: dict[str, Any]) -> dict[str, Any]:
    max_hr = model["athlete_max_hr"]
    easy_pace = model["easy_pace_sec"]

    def classify(a):
        return classify_run(a.get("average_heartrate"), a.get("average_speed"), max_hr, easy_pace)

    # 14-day gray-zone counts
    cutoff = now - timedelta(days=14)
    counts = {"easy": 0, "gray": 0, "hard": 0, "unknown": 0}
    for a in acts:
        if a.get("start_date") and a["start_date"] >= cutoff:
            counts[classify(a)["zone"]] += 1
    classified = counts["easy"] + counts["gray"] + counts["hard"]
    pct_gray_14d = round(100 * counts["gray"] / classified) if classified else None
    pct_easy_14d = round(100 * counts["easy"] / classified) if classified else None
    pct_non_easy_14d = (100 - pct_easy_14d) if pct_easy_14d is not None else None

    # Weekly gray% trend (last 8 weeks)
    trend = []
    for w in range(7, -1, -1):
        start = now - timedelta(days=7 * (w + 1))
        end = now - timedelta(days=7 * w)
        wc = {"easy": 0, "gray": 0, "hard": 0}
        for a in acts:
            if a.get("start_date") and start <= a["start_date"] < end:
                z = classify(a)["zone"]
                if z in wc:
                    wc[z] += 1
        tot = sum(wc.values())
        trend.append({"weeks_ago": w, "runs": tot,
                      "pct_gray": round(100 * wc["gray"] / tot) if tot else None})

    # Recent runs tagged (last 20)
    recent = []
    for a in _recent_sorted(acts)[:20]:
        c = classify(a)
        recent.append({
            "id": a["id"], "name": a.get("name"),
            "start_date": a["start_date"].isoformat() if a.get("start_date") else None,
            "distance_km": round((a.get("distance") or 0) / 1000.0, 2),
            "pace_sec": _pace_sec_per_km(a.get("average_speed")),
            "avg_hr": a.get("average_heartrate"),
            "zone": c["zone"], "basis": c["basis"],
        })

    return {
        "pct_gray_14d": pct_gray_14d,
        "pct_easy_14d": pct_easy_14d,
        "pct_non_easy_14d": pct_non_easy_14d,
        "classified_14d": classified,
        "counts_14d": counts,
        "trend_weekly": trend,
        "recent_runs": recent,
    }


def build_warnings(acts: list[dict[str, Any]], now: datetime, model: dict[str, Any],
                   gray: dict[str, Any]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    ceiling = model["easy_hr_ceiling"]
    easy_pace = model["easy_pace_sec"]

    # ACWR guardrail — but a thin baseline (post-layoff) makes ACWR huge and
    # alarmist, so reframe it as a comeback note until the baseline is real.
    acwr = model.get("acwr")
    chronic = model.get("chronic_load_28d_km") or 0
    if acwr is not None and acwr > 1.3:
        if chronic < 5:
            warnings.append({
                "level": "info", "code": "comeback_ramp",
                "title": "You're ramping up from a break",
                "detail": "Your weekly volume is still rebuilding, so load ratios look "
                          "extreme by design. Keep weekly increases ≤10% and don't chase "
                          "old paces yet.",
            })
        else:
            warnings.append({
                "level": "danger", "code": "acwr_high",
                "title": f"Training load spiking (ACWR {acwr})",
                "detail": "Your last 7 days are well above your 4-week baseline — a classic "
                          "injury-risk window. Take an easy or down week.",
            })

    # Progression cap on the long run
    long28 = model["longest_run_28d_km"]
    if long28 > 0:
        cap = round(long28 + 1.0, 1)
        warnings.append({
            "level": "info", "code": "long_run_cap",
            "title": f"Cap your next long run at ~{cap} km",
            "detail": f"Longest run in the last 28 days is {long28} km. Grow the long run "
                      f"by ≤1 km/week to stay clear of the overload that causes injuries.",
        })

    # One-gear trap — keyed on how little is genuinely easy (with HR, hard runs
    # classify as 'hard', not 'gray', so low-easy is the real signal).
    non_easy = gray.get("pct_non_easy_14d")
    if gray.get("classified_14d", 0) >= 3 and non_easy is not None and non_easy >= 80:
        warnings.append({
            "level": "warn", "code": "one_gear_trap",
            "title": f"{non_easy}% of recent runs are moderate-or-harder",
            "detail": "The one-gear trap: too hard to recover from, too slow to build top-end "
                      f"speed. Aim for ~80% genuinely easy (≤{ceiling} bpm"
                      + (f", ~{_fmt_pace(easy_pace)}/km" if easy_pace else "") + ").",
        })

    # Last 3 runs all non-easy -> force easy
    recent = gray.get("recent_runs", [])[:3]
    if len(recent) == 3 and all(r["zone"] in ("gray", "hard") for r in recent):
        warnings.append({
            "level": "warn", "code": "force_easy",
            "title": "Make your next run genuinely easy",
            "detail": f"Your last 3 runs were all hard/gray. Next one easy: keep HR ≤{ceiling} bpm"
                      + (f" and pace around {_fmt_pace(easy_pace)}/km" if easy_pace else "")
                      + " — if HR climbs, slow down, ignore the pace.",
        })

    # No easy run at all in the 14-day window
    counts = gray.get("counts_14d", {})
    if (counts.get("easy", 0) == 0
            and (counts.get("gray", 0) + counts.get("hard", 0)) >= 3):
        warnings.append({
            "level": "warn", "code": "no_easy",
            "title": "No easy runs in the last 2 weeks",
            "detail": "Every recent run has been moderate-or-harder. Recovery is where "
                      "fitness is built — schedule at least one easy run this week.",
        })

    return warnings


def _fmt_pace(sec: Optional[float]) -> str:
    if not sec:
        return "?"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def training_report(acts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    model = build_fitness_model(acts, now)
    gray = gray_zone_summary(acts, now, model)
    warnings = build_warnings(acts, now, model, gray)
    return {"fitness_model": model, "gray_zone": gray, "warnings": warnings}
