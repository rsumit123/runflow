"""Async wrapper over python-garminconnect (which is synchronous)."""
import asyncio
from typing import Any

import garmin_transform as gt
from garmin_auth import get_garmin


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
