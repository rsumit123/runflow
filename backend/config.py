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
