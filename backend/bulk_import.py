"""
Bulk import module for Strava data export archives.

Handles importing activities from Strava's "Download Your Data" ZIP/directory export,
which contains an activities.csv and individual activity files (.gpx, .fit, .tcx).
"""

import csv
import io
import logging
import math
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gpxpy
import gpxpy.gpx

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Activity, Split, Stream

logger = logging.getLogger(__name__)

RUNNING_ACTIVITY_TYPES = {"Run", "TrailRun", "VirtualRun"}


def encode_polyline(coords: list, every_n: int = 5) -> str:
    """Encode a list of [lat, lng] into a Google summary polyline, simplified."""
    if len(coords) <= 20:
        points = coords
    else:
        points = [coords[i] for i in range(0, len(coords), every_n)]
        if points[-1] != coords[-1]:
            points.append(coords[-1])

    result = ''
    prev_lat = 0
    prev_lng = 0
    for lat, lng in points:
        lat_int = round(lat * 1e5)
        lng_int = round(lng * 1e5)
        d_lat = lat_int - prev_lat
        d_lng = lng_int - prev_lng
        prev_lat = lat_int
        prev_lng = lng_int
        for v in [d_lat, d_lng]:
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                result += chr((0x20 | (v & 0x1f)) + 63)
                v >>= 5
            result += chr(v + 63)
    return result

# ---------------------------------------------------------------------------
# Haversine helper
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in meters between two GPS coordinates."""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# GPX parsing
# ---------------------------------------------------------------------------

def _parse_gpx(filepath: str) -> dict[str, Any] | None:
    """
    Parse a GPX file and return trackpoints with lat, lng, elevation, time.
    Returns a dict with keys: trackpoints, latlng, altitude, distance, time.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            gpx = gpxpy.parse(f)
    except Exception as exc:
        logger.warning("Failed to parse GPX file %s: %s", filepath, exc)
        return None

    trackpoints = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                trackpoints.append({
                    "lat": point.latitude,
                    "lng": point.longitude,
                    "elevation": point.elevation,
                    "time": point.time,
                })

    if not trackpoints:
        return None

    # Build stream-like arrays
    latlng = []
    altitude = []
    distance_stream = []
    time_stream = []

    cumulative_distance = 0.0
    start_time = trackpoints[0]["time"]

    for i, tp in enumerate(trackpoints):
        latlng.append([tp["lat"], tp["lng"]])
        altitude.append(tp["elevation"] if tp["elevation"] is not None else 0.0)

        if i > 0:
            prev = trackpoints[i - 1]
            d = _haversine(prev["lat"], prev["lng"], tp["lat"], tp["lng"])
            cumulative_distance += d

        distance_stream.append(round(cumulative_distance, 2))

        if start_time and tp["time"]:
            elapsed = (tp["time"] - start_time).total_seconds()
            time_stream.append(int(elapsed))
        else:
            time_stream.append(0)

    return {
        "trackpoints": trackpoints,
        "latlng": latlng,
        "altitude": altitude,
        "distance": distance_stream,
        "time": time_stream,
    }


# ---------------------------------------------------------------------------
# FIT parsing (optional — graceful fallback)
# ---------------------------------------------------------------------------

def _parse_fit(filepath: str) -> dict[str, Any] | None:
    """
    Parse a FIT file using fitdecode. Returns same structure as _parse_gpx.
    Returns None if fitdecode is not installed or parsing fails.
    """
    try:
        import fitdecode
    except ImportError:
        logger.info("fitdecode not installed — skipping FIT file %s", filepath)
        return None

    try:
        trackpoints = []
        with fitdecode.FitReader(filepath) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name != "record":
                    continue

                lat_field = frame.get_field("position_lat")
                lng_field = frame.get_field("position_long")
                if lat_field is None or lng_field is None:
                    continue
                if lat_field.value is None or lng_field.value is None:
                    continue

                # FIT stores lat/lng as semicircles — convert to degrees
                lat = lat_field.value * (180.0 / 2**31)
                lng = lng_field.value * (180.0 / 2**31)

                elevation = None
                alt_field = frame.get_field("altitude")
                if alt_field is not None and alt_field.value is not None:
                    elevation = float(alt_field.value)

                timestamp = None
                ts_field = frame.get_field("timestamp")
                if ts_field is not None and ts_field.value is not None:
                    timestamp = ts_field.value
                    if isinstance(timestamp, datetime) and timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)

                trackpoints.append({
                    "lat": lat,
                    "lng": lng,
                    "elevation": elevation,
                    "time": timestamp,
                })

        if not trackpoints:
            return None

        # Build stream-like arrays
        latlng = []
        altitude = []
        distance_stream = []
        time_stream = []

        cumulative_distance = 0.0
        start_time = trackpoints[0]["time"]

        for i, tp in enumerate(trackpoints):
            latlng.append([tp["lat"], tp["lng"]])
            altitude.append(tp["elevation"] if tp["elevation"] is not None else 0.0)

            if i > 0:
                prev = trackpoints[i - 1]
                d = _haversine(prev["lat"], prev["lng"], tp["lat"], tp["lng"])
                cumulative_distance += d

            distance_stream.append(round(cumulative_distance, 2))

            if start_time and tp["time"]:
                elapsed = (tp["time"] - start_time).total_seconds()
                time_stream.append(int(elapsed))
            else:
                time_stream.append(0)

        return {
            "trackpoints": trackpoints,
            "latlng": latlng,
            "altitude": altitude,
            "distance": distance_stream,
            "time": time_stream,
        }

    except Exception as exc:
        logger.warning("Failed to parse FIT file %s: %s", filepath, exc)
        return None


# ---------------------------------------------------------------------------
# Split calculation from GPS data
# ---------------------------------------------------------------------------

def _calculate_splits(parsed_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Calculate per-kilometer splits from parsed GPS data.
    Each split covers one kilometer (the last split may be shorter).
    Returns a list of split dicts compatible with the Split model.
    """
    distance_stream = parsed_data["distance"]
    time_stream = parsed_data["time"]
    altitude_stream = parsed_data["altitude"]

    if not distance_stream or len(distance_stream) < 2:
        return []

    total_distance = distance_stream[-1]
    if total_distance < 10:  # Less than 10 meters — skip
        return []

    splits = []
    split_number = 1
    km_boundary = 1000.0  # next km boundary in meters
    split_start_idx = 0

    for i in range(1, len(distance_stream)):
        if distance_stream[i] >= km_boundary or i == len(distance_stream) - 1:
            # This point crosses the km boundary or is the last point
            is_last = i == len(distance_stream) - 1 and distance_stream[i] < km_boundary

            split_distance = distance_stream[i] - distance_stream[split_start_idx]
            split_time = time_stream[i] - time_stream[split_start_idx]

            # Elevation change for this split
            elev_start = altitude_stream[split_start_idx] if altitude_stream[split_start_idx] is not None else 0
            elev_end = altitude_stream[i] if altitude_stream[i] is not None else 0
            elevation_diff = elev_end - elev_start

            # Average speed in m/s
            avg_speed = split_distance / split_time if split_time > 0 else 0.0

            # Only include the last partial split if it's substantial (> 100m)
            if is_last and split_distance < 100:
                break

            splits.append({
                "split_number": split_number,
                "distance": round(split_distance, 1),
                "moving_time": int(split_time),
                "elapsed_time": int(split_time),
                "average_speed": round(avg_speed, 3),
                "pace_zone": 0,
                "elevation_difference": round(elevation_diff, 1),
                "average_heartrate": None,
            })

            split_number += 1
            split_start_idx = i
            km_boundary += 1000.0

    return splits


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_activity_date(date_str: str) -> datetime | None:
    """Parse the Activity Date from Strava's CSV export."""
    # Strava export format is typically: "Oct 1, 2023, 8:30:00 AM" or similar
    formats = [
        "%b %d, %Y, %I:%M:%S %p",
        "%b %d, %Y, %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d %b %Y, %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y, %I:%M:%S %p",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    logger.warning("Could not parse activity date: %s", date_str)
    return None


def _parse_csv_activities(csv_path: str) -> list[dict[str, Any]]:
    """
    Parse activities.csv and return a list of activity dicts for running activities.
    """
    activities = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            activity_type = row.get("Activity Type", "").strip()
            if activity_type not in RUNNING_ACTIVITY_TYPES:
                continue

            activity_id_str = row.get("Activity ID", "").strip()
            if not activity_id_str:
                continue

            try:
                activity_id = int(activity_id_str)
            except ValueError:
                logger.warning("Invalid Activity ID: %s", activity_id_str)
                continue

            # Parse numeric fields safely
            def _float(key: str) -> float | None:
                val = row.get(key, "").strip()
                if not val:
                    return None
                try:
                    return float(val.replace(",", ""))
                except ValueError:
                    return None

            def _int_from_str(key: str) -> int | None:
                val = _float(key)
                return int(val) if val is not None else None

            # Distance in CSV is in meters (or km depending on export settings)
            # Strava exports typically use the user's preferred unit
            distance = _float("Distance")

            # Elapsed Time and Moving Time may be in seconds or HH:MM:SS
            elapsed_time_str = row.get("Elapsed Time", "").strip()
            moving_time_str = row.get("Moving Time", "").strip()

            def _parse_time(val: str) -> int | None:
                if not val:
                    return None
                try:
                    return int(float(val))
                except ValueError:
                    # Try HH:MM:SS
                    parts = val.split(":")
                    if len(parts) == 3:
                        try:
                            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                        except ValueError:
                            pass
                    return None

            activities.append({
                "id": activity_id,
                "name": row.get("Activity Name", "").strip() or f"Activity {activity_id}",
                "sport_type": activity_type,
                "distance": distance,
                "moving_time": _parse_time(moving_time_str),
                "elapsed_time": _parse_time(elapsed_time_str),
                "start_date": _parse_activity_date(row.get("Activity Date", "")),
                "total_elevation_gain": _float("Elevation Gain"),
                "average_speed": None,  # Will calculate from distance/time
                "max_speed": None,
                "elev_high": _float("Elevation High") if "Elevation High" in row else None,
                "elev_low": _float("Elevation Low") if "Elevation Low" in row else None,
                "filename": row.get("Filename", "").strip(),
            })

    return activities


# ---------------------------------------------------------------------------
# Find activity file (GPX, FIT, or TCX)
# ---------------------------------------------------------------------------

def _find_activity_file(base_dir: str, filename: str) -> str | None:
    """
    Given the Filename from CSV (e.g. 'activities/12345.gpx' or '12345.gpx.gz'),
    find the actual file in the export directory.
    """
    if not filename:
        return None

    # The filename in CSV is relative to the export root
    full_path = os.path.join(base_dir, filename)
    if os.path.exists(full_path):
        return full_path

    # Try without .gz extension (user may have decompressed)
    if full_path.endswith(".gz"):
        unzipped = full_path[:-3]
        if os.path.exists(unzipped):
            return unzipped

    # Try looking in activities/ subdirectory
    basename = os.path.basename(filename)
    activities_dir = os.path.join(base_dir, "activities")
    if os.path.isdir(activities_dir):
        candidate = os.path.join(activities_dir, basename)
        if os.path.exists(candidate):
            return candidate
        if basename.endswith(".gz"):
            candidate_unzipped = os.path.join(activities_dir, basename[:-3])
            if os.path.exists(candidate_unzipped):
                return candidate_unzipped

    return None


def _decompress_if_needed(filepath: str) -> str:
    """If the file is .gz, decompress it to a temp location and return the new path."""
    if not filepath.endswith(".gz"):
        return filepath

    import gzip
    import tempfile

    out_path = filepath[:-3]  # Remove .gz
    # Check if already decompressed
    if os.path.exists(out_path):
        return out_path

    try:
        with gzip.open(filepath, "rb") as f_in:
            content = f_in.read()
        # Write next to the original file
        with open(out_path, "wb") as f_out:
            f_out.write(content)
        return out_path
    except Exception as exc:
        logger.warning("Failed to decompress %s: %s", filepath, exc)
        return filepath


def _parse_activity_file(filepath: str) -> dict[str, Any] | None:
    """Parse an activity file (GPX or FIT) and return trackpoint data."""
    filepath = _decompress_if_needed(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".gpx":
        return _parse_gpx(filepath)
    elif ext == ".fit":
        return _parse_fit(filepath)
    elif ext == ".tcx":
        # TCX is XML-based; we could parse it but GPX and FIT cover most cases
        logger.info("TCX parsing not implemented — skipping %s", filepath)
        return None
    else:
        logger.info("Unsupported file format %s — skipping %s", ext, filepath)
        return None


# ---------------------------------------------------------------------------
# Extract ZIP if needed
# ---------------------------------------------------------------------------

def extract_zip_if_needed(path: str) -> str:
    """
    If `path` points to a ZIP file, extract it to a sibling directory and return
    the extraction directory. Otherwise return `path` as-is.
    """
    if os.path.isfile(path) and zipfile.is_zipfile(path):
        extract_dir = path.rstrip(".zip").rstrip(".ZIP") + "_extracted"
        if not os.path.isdir(extract_dir):
            logger.info("Extracting ZIP archive to %s", extract_dir)
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(extract_dir)
        return extract_dir
    return path


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

async def import_from_export(
    session: AsyncSession,
    export_path: str,
    progress: dict[str, Any] | None = None,
) -> dict[str, int]:
    """
    Import running activities from a Strava data export.

    Args:
        session: SQLAlchemy async session
        export_path: Path to the extracted export directory or ZIP file
        progress: Optional dict to update with progress info (mutated in place)

    Returns:
        Dict with counts: imported, skipped, errors, already_exists
    """
    # Handle ZIP file
    base_dir = extract_zip_if_needed(export_path)

    # Locate activities.csv
    csv_path = os.path.join(base_dir, "activities.csv")
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"activities.csv not found in {base_dir}")

    # Parse CSV to get running activities
    csv_activities = _parse_csv_activities(csv_path)
    total = len(csv_activities)

    if progress is not None:
        progress["total"] = total
        progress["imported"] = 0
        progress["status"] = "running"

    imported = 0
    skipped = 0
    errors = 0
    already_exists = 0

    for idx, act_data in enumerate(csv_activities):
        activity_id = act_data["id"]

        # Check if already in DB with detailed data
        existing = await session.get(Activity, activity_id)
        if existing and existing.has_detailed_data:
            already_exists += 1
            if progress is not None:
                progress["imported"] = imported
                progress["current"] = idx + 1
            continue

        # Calculate average_speed from distance and moving_time
        if act_data["distance"] and act_data["moving_time"] and act_data["moving_time"] > 0:
            act_data["average_speed"] = act_data["distance"] / act_data["moving_time"]

        # Upsert activity summary
        if existing is None:
            act = Activity(id=activity_id)
            session.add(act)
        else:
            act = existing

        act.name = act_data["name"]
        act.sport_type = act_data["sport_type"]
        act.distance = act_data["distance"]
        act.moving_time = act_data["moving_time"]
        act.elapsed_time = act_data["elapsed_time"]
        act.start_date = act_data["start_date"]
        act.average_speed = act_data["average_speed"]
        act.max_speed = act_data["max_speed"]
        act.total_elevation_gain = act_data["total_elevation_gain"]
        act.elev_high = act_data.get("elev_high")
        act.elev_low = act_data.get("elev_low")

        # Try to parse the activity file for GPS data, splits, and streams
        filename = act_data.get("filename", "")
        activity_file = _find_activity_file(base_dir, filename)
        parsed = None

        if activity_file:
            try:
                parsed = _parse_activity_file(activity_file)
            except Exception as exc:
                logger.warning("Error parsing file for activity %s: %s", activity_id, exc)

        if parsed:
            # Set start/end latlng and generate summary polyline
            if parsed["latlng"]:
                act.start_latlng = parsed["latlng"][0]
                act.end_latlng = parsed["latlng"][-1]
                act.map_summary_polyline = encode_polyline(parsed["latlng"])

            # Remove old splits
            existing_splits = await session.execute(
                select(Split).where(Split.activity_id == activity_id)
            )
            for old in existing_splits.scalars().all():
                await session.delete(old)

            # Calculate and store splits
            splits = _calculate_splits(parsed)
            for sd in splits:
                split = Split(
                    activity_id=activity_id,
                    split_number=sd["split_number"],
                    distance=sd["distance"],
                    moving_time=sd["moving_time"],
                    elapsed_time=sd["elapsed_time"],
                    average_speed=sd["average_speed"],
                    pace_zone=sd["pace_zone"],
                    elevation_difference=sd["elevation_difference"],
                    average_heartrate=sd["average_heartrate"],
                )
                session.add(split)

            # Remove old streams
            existing_streams = await session.execute(
                select(Stream).where(Stream.activity_id == activity_id)
            )
            for old in existing_streams.scalars().all():
                await session.delete(old)

            # Store streams
            stream_types_data = {
                "latlng": parsed["latlng"],
                "altitude": parsed["altitude"],
                "distance": parsed["distance"],
                "time": parsed["time"],
            }
            for stream_type, data in stream_types_data.items():
                if data:
                    stream = Stream(
                        activity_id=activity_id,
                        stream_type=stream_type,
                        data=data,
                    )
                    session.add(stream)

            act.has_detailed_data = True
            imported += 1
        else:
            # No GPS file found — import summary only
            act.has_detailed_data = False
            imported += 1
            if not activity_file:
                logger.info("No activity file found for activity %s (filename: %s)", activity_id, filename)

        # Commit periodically
        if (imported + already_exists) % 10 == 0:
            await session.commit()
            logger.info("Bulk import progress: %d/%d", idx + 1, total)

        if progress is not None:
            progress["imported"] = imported
            progress["current"] = idx + 1

    await session.commit()

    if progress is not None:
        progress["status"] = "completed"
        progress["imported"] = imported

    return {
        "imported": imported,
        "skipped_non_running": skipped,
        "already_exists": already_exists,
        "errors": errors,
        "total_in_csv": total,
    }
