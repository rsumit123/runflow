"""Async wrapper over python-garminconnect (which is synchronous)."""
import asyncio
import logging
from typing import Any

import garmin_transform as gt
from garmin_auth import get_garmin

logger = logging.getLogger(__name__)


class GarminClient:
    async def get_recent_running(self, limit: int = 20, start: int = 0) -> list[dict[str, Any]]:
        g = get_garmin()
        acts = await asyncio.to_thread(g.get_activities, start, limit)
        return [a for a in (acts or [])
                if gt.is_garmin_running((a.get("activityType") or {}).get("typeKey"))]

    async def get_splits(self, activity_id: int) -> dict[str, Any]:
        g = get_garmin()
        return await asyncio.to_thread(g.get_activity_splits, activity_id)

    async def get_details(self, activity_id: int) -> dict[str, Any]:
        g = get_garmin()
        return await asyncio.to_thread(g.get_activity_details, activity_id)

    async def get_hr_zones(self, activity_id: int) -> Any:
        g = get_garmin()
        return await asyncio.to_thread(g.get_activity_hr_in_timezones, activity_id)

    async def push_workout(self, name: str, steps: list[dict[str, Any]],
                           date_str: str) -> dict[str, Any]:
        """Upload a structured workout to Garmin Connect and schedule it on `date_str`
        (YYYY-MM-DD) so it syncs to the watch. Returns {"workout_id": int}."""
        import garmin_workout as gw
        g = get_garmin()
        workout = gw.build_running_workout(name, steps)
        res = await asyncio.to_thread(g.upload_running_workout, workout)
        wid = (res or {}).get("workoutId") or (res or {}).get("workoutid")
        if not wid:
            raise RuntimeError(f"Garmin did not return a workout id: {res}")
        await asyncio.to_thread(g.schedule_workout, wid, date_str)
        return {"workout_id": int(wid)}

    async def remove_workout(self, workout_id: int) -> None:
        """Delete a workout from Garmin Connect (also removes it from the calendar)."""
        g = get_garmin()
        await asyncio.to_thread(g.delete_workout, workout_id)

    async def wellness(self, day: str) -> dict[str, Any]:
        """Everything Garmin knows about how recovered the runner is today.

        Each call is independent: a watch that doesn't report Body Battery
        shouldn't cost us the sleep data, so one failure never sinks the rest.
        """
        g = get_garmin()

        async def _try(name: str, *args):
            try:
                return await asyncio.to_thread(getattr(g, name), *args)
            except Exception as exc:  # noqa: BLE001 — a missing metric is not an error
                logger.warning("Garmin %s failed for %s: %s", name, day, exc)
                return None

        readiness, hrv, sleep, bb, rhr = await asyncio.gather(
            _try("get_training_readiness", day),
            _try("get_hrv_data", day),
            _try("get_sleep_data", day),
            _try("get_body_battery", day, day),
            _try("get_rhr_day", day),
        )
        return {"readiness": readiness, "hrv": hrv, "sleep": sleep,
                "body_battery": bb, "rhr": rhr}
