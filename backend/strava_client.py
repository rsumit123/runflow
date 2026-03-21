import asyncio
import logging
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

STREAM_TYPES = ["latlng", "altitude", "distance", "time", "velocity_smooth"]


class StravaClient:
    """Async Strava API client with automatic token refresh and rate-limit handling."""

    def __init__(self) -> None:
        self.access_token: str = config.STRAVA_ACCESS_TOKEN
        self.refresh_token: str = config.STRAVA_REFRESH_TOKEN
        self.client_id: str = config.STRAVA_CLIENT_ID
        self.client_secret: str = config.STRAVA_CLIENT_SECRET
        self.base_url: str = config.STRAVA_API_BASE
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------
    async def refresh_access_token(self) -> str:
        """Use the refresh token to obtain a new access token."""
        client = await self._get_client()
        resp = await client.post(
            config.STRAVA_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        # Persist new tokens to .env and in-memory config
        config.update_env_tokens(self.access_token, self.refresh_token)
        logger.info("Strava access token refreshed successfully.")
        return self.access_token

    # ------------------------------------------------------------------
    # Core request helper with 401 retry and rate-limit back-off
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_retries: int = 5,
    ) -> Any:
        """
        Make an authenticated request to the Strava API.
        - On 401: refresh token once and retry.
        - On 429: respect Retry-After / exponential back-off.
        """
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        retried_auth = False
        backoff = 15  # initial backoff seconds for rate limiting

        for attempt in range(max_retries):
            resp = await client.request(method, url, headers=self._auth_headers(), params=params)

            if resp.status_code == 401 and not retried_auth:
                logger.warning("Received 401 — refreshing access token.")
                await self.refresh_access_token()
                retried_auth = True
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", backoff))
                wait = max(retry_after, backoff)
                logger.warning("Rate limited (429). Waiting %d seconds (attempt %d).", wait, attempt + 1)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 900)  # cap at 15 min
                continue

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"Strava API request failed after {max_retries} retries: {method} {path}")

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------
    async def get_activities(
        self,
        page: int = 1,
        per_page: int = 50,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        """List athlete activities (summary level). after/before are epoch timestamps."""
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        return await self._request("GET", "/athlete/activities", params=params)

    async def get_activity_detail(self, activity_id: int) -> dict[str, Any]:
        """Get a detailed activity by ID (includes splits_metric etc.)."""
        return await self._request("GET", f"/activities/{activity_id}")

    async def get_activity_streams(
        self,
        activity_id: int,
        stream_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch streams for an activity."""
        types = stream_types or STREAM_TYPES
        keys = ",".join(types)
        return await self._request(
            "GET",
            f"/activities/{activity_id}/streams",
            params={"keys": keys, "key_type": "time"},
        )
