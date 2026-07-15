"""Pure transforms: Garmin API payloads -> RunFlow field dicts / stream arrays.
No I/O, no DB, no network — safe to unit test."""
from __future__ import annotations
from datetime import datetime
from typing import Any

from config import GARMIN_METRIC_KEYS, GARMIN_RUNNING_TYPES


def is_garmin_running(type_key: str | None) -> bool:
    if not type_key:
        return False
    return type_key in GARMIN_RUNNING_TYPES or type_key.endswith("_running")


def _parse_garmin_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _as_int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def summary_to_activity_fields(summary: dict[str, Any]) -> dict[str, Any]:
    at = summary.get("activityType") or {}
    lat0, lon0 = summary.get("startLatitude"), summary.get("startLongitude")
    lat1, lon1 = summary.get("endLatitude"), summary.get("endLongitude")
    return {
        "id": summary["activityId"],
        "name": summary.get("activityName"),
        "sport_type": at.get("typeKey"),
        "distance": summary.get("distance"),
        "moving_time": _as_int(summary.get("movingDuration")),
        "elapsed_time": _as_int(summary.get("duration")),
        "start_date": _parse_garmin_dt(summary.get("startTimeGMT")),
        "average_speed": summary.get("averageSpeed"),
        "max_speed": summary.get("maxSpeed"),
        "total_elevation_gain": summary.get("elevationGain"),
        "elev_high": summary.get("maxElevation"),
        "elev_low": summary.get("minElevation"),
        "start_latlng": [lat0, lon0] if lat0 is not None else None,
        "end_latlng": [lat1, lon1] if lat1 is not None else None,
        "average_heartrate": summary.get("averageHR"),
        "max_heartrate": summary.get("maxHR"),
        "average_cadence": summary.get("averageRunningCadenceInStepsPerMinute"),
        "source": "garmin",
        "aerobic_te": summary.get("aerobicTrainingEffect"),
        "anaerobic_te": summary.get("anaerobicTrainingEffect"),
        "training_effect_label": summary.get("trainingEffectLabel"),
        "training_load": summary.get("activityTrainingLoad"),
    }


def laps_to_splits(splits_payload: dict[str, Any]) -> list[dict[str, Any]]:
    laps = splits_payload.get("lapDTOs") or []
    out = []
    for i, lap in enumerate(laps):
        out.append({
            "split_number": i + 1,
            "distance": lap.get("distance"),
            "moving_time": _as_int(lap.get("movingDuration") or lap.get("duration")),
            "elapsed_time": _as_int(lap.get("duration")),
            "average_speed": lap.get("averageSpeed"),
            "elevation_difference": lap.get("elevationGain"),
            "average_heartrate": lap.get("averageHR"),
            "average_cadence": lap.get("averageRunCadence"),
        })
    return out


def _index_map(details: dict[str, Any]) -> dict[str, int]:
    out = {}
    for d in details.get("metricDescriptors") or []:
        key = d.get("key")
        idx = d.get("metricsIndex")
        if key is not None and idx is not None:
            out[key] = idx
    return out


def _column(rows: list[dict], idx: int) -> list:
    return [(r.get("metrics") or [None])[idx] if idx < len(r.get("metrics") or []) else None
            for r in rows]


def details_to_streams(details: dict[str, Any]) -> dict[str, list]:
    """Return {stream_type: aligned array}. Only includes metrics present."""
    rows = details.get("activityDetailMetrics") or []
    imap = _index_map(details)
    K = GARMIN_METRIC_KEYS
    streams: dict[str, list] = {}

    def col(name: str):
        key = K.get(name)
        return _column(rows, imap[key]) if key in imap else None

    lats, lons = col("latitude"), col("longitude")
    if lats and lons:
        streams["latlng"] = [[a, b] for a, b in zip(lats, lons)]

    ts = col("timestamp")
    if ts and ts[0] is not None:
        t0 = ts[0]
        streams["time"] = [int((t - t0) / 1000) if t is not None else None for t in ts]

    for name, stype in (("distance", "distance"), ("speed", "velocity_smooth"),
                        ("heartrate", "heartrate"), ("cadence", "cadence"),
                        ("stride_length", "stride_length"),
                        ("ground_contact_time", "ground_contact_time"),
                        ("vertical_oscillation", "vertical_oscillation")):
        c = col(name)
        if c and any(v is not None for v in c):
            streams[stype] = c
    return streams


def hr_zones(zones_payload: Any) -> list[dict[str, Any]]:
    rows = zones_payload if isinstance(zones_payload, list) else []
    out = []
    for z in rows:
        out.append({
            "zone": z.get("zoneNumber"),
            "secs": z.get("secsInZone"),
            "low_bpm": z.get("zoneLowBoundary"),
        })
    return out


def running_dynamics_summary(streams: dict[str, list]) -> dict | None:
    def avg(name):
        vals = [v for v in streams.get(name, []) if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None
    d = {
        "stride_length": avg("stride_length"),
        "ground_contact_time": avg("ground_contact_time"),
        "vertical_oscillation": avg("vertical_oscillation"),
    }
    return d if any(v is not None for v in d.values()) else None
