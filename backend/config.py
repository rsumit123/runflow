import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env — check ENV_PATH env var first, then project root (one level up from backend/)
ENV_PATH = Path(os.getenv("ENV_PATH", Path(__file__).resolve().parent.parent / ".env"))
load_dotenv(dotenv_path=ENV_PATH)

STRAVA_CLIENT_ID = os.getenv("strava_client_id", "")
STRAVA_CLIENT_SECRET = os.getenv("strava_client_secret", "")

# Strava OAuth URLs
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

# OAuth redirect (where Strava sends the user back after granting access)
STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI",
                                "http://localhost:8000/api/strava/callback")

# Webhook
def _int_env(name: str) -> int | None:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return None


# The id Strava returns when the webhook subscription is created. When set, events
# that carry a different subscription_id are ignored. Empty means unchecked.
STRAVA_SUBSCRIPTION_ID = _int_env("STRAVA_SUBSCRIPTION_ID")
STRAVA_WEBHOOK_VERIFY_TOKEN = os.getenv("strava_webhook_verify_token", "runflow_webhook_2024")


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

# ---------------------------------------------------------------------------
# MCP connector
# ---------------------------------------------------------------------------
# The secret lives in the mount path, because Claude app custom connectors
# accept only authless or OAuth servers — there is no field for a bearer token
# or a custom header. Unset means the MCP server is not mounted at all, so a
# missing secret fails closed rather than exposing an open endpoint.
MCP_SECRET = os.getenv("MCP_SECRET", "").strip()

# A run note is context, not a journal — capped so rows stay small and the MCP
# tool output stays readable. Enforced by both the REST endpoint and the tool.
NOTE_MAX_CHARS = 2000

# Past this age, a run with no weather-archive row never will have one — stop
# retrying it. Also bounds how far back the self-healing retry sweeps.
WEATHER_GIVE_UP_DAYS = 30

# --- Accounts -------------------------------------------------------------
# Fernet key for provider tokens at rest. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CREDENTIALS_KEY = os.getenv("CREDENTIALS_KEY", "").strip()
# HMAC key for OAuth `state`.
SESSION_STATE_SECRET = os.getenv("SESSION_STATE_SECRET", "").strip()
SESSION_COOKIE_NAME = "rf_session"
SESSION_TTL_DAYS = 30
# Secure cookies need HTTPS; set to 0 only for local http development.
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "1") != "0"
ALLOWED_ORIGINS = ["https://runflow.skdev.one", "http://localhost:3000"]
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "http://localhost:8000").rstrip("/")
