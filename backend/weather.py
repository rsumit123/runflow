"""Current + near-term conditions from Open-Meteo (no API key, no account)."""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8.0


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
