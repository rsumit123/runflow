"""Authenticated Garmin client from a token store. No password on the server."""
import logging, os
from garminconnect import Garmin
import config

logger = logging.getLogger(__name__)
_client: Garmin | None = None


def get_garmin() -> Garmin:
    """Return a logged-in Garmin client, loading cached tokens. Raises if none."""
    global _client
    if _client is not None:
        return _client
    store = config.GARMIN_TOKENSTORE
    if not os.path.isdir(store) or not os.listdir(store):
        raise RuntimeError(
            f"Garmin token store empty/missing at {store}. "
            "Run the local login and copy the token directory to the server "
            "(see GARMIN.md)."
        )
    g = Garmin()
    g.login(store)  # loads cached OAuth tokens; no credentials needed
    _client = g
    logger.info("Garmin client authenticated from token store.")
    return _client


def reset() -> None:
    global _client
    _client = None
