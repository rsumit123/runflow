import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env — check ENV_PATH env var first, then project root (one level up from backend/)
ENV_PATH = Path(os.getenv("ENV_PATH", Path(__file__).resolve().parent.parent / ".env"))
load_dotenv(dotenv_path=ENV_PATH)

STRAVA_CLIENT_ID = os.getenv("strava_client_id", "")
STRAVA_CLIENT_SECRET = os.getenv("strava_client_secret", "")
STRAVA_ACCESS_TOKEN = os.getenv("strava_access_token", "")
STRAVA_REFRESH_TOKEN = os.getenv("strava_refresh_token", "")

# Strava OAuth URLs
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

# OAuth redirect
OAUTH_REDIRECT_URI = "http://localhost:8000/api/auth/callback"

# Webhook
STRAVA_WEBHOOK_VERIFY_TOKEN = os.getenv("strava_webhook_verify_token", "runflow_webhook_2024")


def update_env_tokens(access_token: str, refresh_token: str) -> None:
    """Update the .env file with new tokens."""
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()

    new_lines = []
    found_access = False
    found_refresh = False
    for line in lines:
        if line.startswith("strava_access_token="):
            new_lines.append(f"strava_access_token={access_token}")
            found_access = True
        elif line.startswith("strava_refresh_token="):
            new_lines.append(f"strava_refresh_token={refresh_token}")
            found_refresh = True
        else:
            new_lines.append(line)

    if not found_access:
        new_lines.append(f"strava_access_token={access_token}")
    if not found_refresh:
        new_lines.append(f"strava_refresh_token={refresh_token}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n")

    # Also update the in-memory values
    global STRAVA_ACCESS_TOKEN, STRAVA_REFRESH_TOKEN
    STRAVA_ACCESS_TOKEN = access_token
    STRAVA_REFRESH_TOKEN = refresh_token
    os.environ["strava_access_token"] = access_token
    os.environ["strava_refresh_token"] = refresh_token


# ---------------------------------------------------------------------------
# Garmin
# ---------------------------------------------------------------------------
GARMIN_TOKENSTORE = os.getenv("GARMIN_TOKENSTORE", "/data/garmin_tokens")

# activityDetailMetrics descriptor keys -> RunFlow stream types.
# Confirm/adjust against tests/fixtures/garmin_details.json['metricDescriptors'].
GARMIN_METRIC_KEYS = {
    "latitude": "directLatitude",
    "longitude": "directLongitude",
    "timestamp": "directTimestamp",
    "distance": "sumDistance",
    "speed": "directSpeed",
    "heartrate": "directHeartRate",
    "cadence": "directRunCadence",
    "stride_length": "directStrideLength",
    "ground_contact_time": "directGroundContactTime",
    "vertical_oscillation": "directVerticalOscillation",
}
GARMIN_RUNNING_TYPES = {"running", "track_running", "trail_running", "treadmill_running"}

# ---------------------------------------------------------------------------
# OpenRouter (coaching narrative) — OpenAI-compatible chat completions
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
