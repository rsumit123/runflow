"""Current + near-term conditions from Open-Meteo (no API key, no account)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT = 8.0
ARCHIVE_TIMEOUT = 60.0


async def archive_hourly(lat: float, lon: float, start_date: str,
                         end_date: str) -> dict[str, tuple[float, float]]:
    """Historical temp + dew point for a whole date range, in ONE request.

    Keyed by the local hour stamp Open-Meteo returns ("2026-02-07T06:00"), so a
    caller can look up the conditions for any past run without a request per run.
    Returns {} on failure — a missing archive must never block anything.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m,dew_point_2m",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=ARCHIVE_TIMEOUT) as client:
            res = await client.get(ARCHIVE_URL, params=params)
            res.raise_for_status()
            body = res.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Weather archive failed (%s..%s): %s", start_date, end_date, exc)
        return {}

    h = body.get("hourly") or {}
    times = h.get("time") or []
    temps = h.get("temperature_2m") or []
    dews = h.get("dew_point_2m") or []
    offset_h = (body.get("utc_offset_seconds") or 0) / 3600.0

    out: dict[str, tuple[float, float]] = {}
    for i, t in enumerate(times):
        if i < len(temps) and i < len(dews) and temps[i] is not None and dews[i] is not None:
            out[t] = (temps[i], dews[i])
    out["_utc_offset_h"] = offset_h  # type: ignore[assignment]
    return out


async def conditions_at_hour(lat: float, lon: float,
                             utc_hour: int) -> Optional[dict[str, Any]]:
    """Conditions at the hour the runner actually runs, given in UTC.

    Reading "now" is wrong whenever they aren't about to head out: open the app
    at 5pm and you'd price a dawn run at the hottest slot of the day. We resolve
    the runner's local hour from the location's own UTC offset, so this works
    anywhere without us hardcoding a timezone.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,cloud_cover",
        "forecast_days": 2,
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.get(BASE_URL, params=params)
            res.raise_for_status()
            body = res.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hourly forecast failed for %s,%s: %s", lat, lon, exc)
        return None

    h = body.get("hourly") or {}
    times = h.get("time") or []
    if not times:
        return None

    offset_h = (body.get("utc_offset_seconds") or 0) / 3600.0
    local_hour = int(round(utc_hour + offset_h)) % 24

    idx = next((i for i, t in enumerate(times) if int(t[11:13]) == local_hour), None)
    if idx is None:
        return None

    def at(key):
        vals = h.get(key) or []
        return vals[idx] if idx < len(vals) else None

    if at("temperature_2m") is None or at("dew_point_2m") is None:
        return None

    return {
        "temp_c": at("temperature_2m"),
        "dew_point_c": at("dew_point_2m"),
        "humidity_pct": at("relative_humidity_2m"),
        "feels_like_c": at("apparent_temperature"),
        "cloud_cover_pct": at("cloud_cover"),
        "local_hour": local_hour,
        "for_time": times[idx],
    }


async def conditions(lat: float, lon: float) -> Optional[dict[str, Any]]:
    """Temperature + dew point right now. None if the service is unreachable —
    a weather outage must never block the training plan."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature",
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            res = await client.get(BASE_URL, params=params)
            res.raise_for_status()
            cur = (res.json() or {}).get("current") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Weather lookup failed for %s,%s: %s", lat, lon, exc)
        return None

    if cur.get("temperature_2m") is None or cur.get("dew_point_2m") is None:
        return None

    return {
        "temp_c": cur["temperature_2m"],
        "dew_point_c": cur["dew_point_2m"],
        "humidity_pct": cur.get("relative_humidity_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
    }
