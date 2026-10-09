"""Weather must keep retrying until the archive actually has the hour.

The archive lags a few days, so a run imported today has no conditions yet. The
backfill must leave such a run retryable — and something must retry it, or the
run stays null forever and every pace comparison silently loses its heat
normalisation.
"""
import tempfile
from datetime import datetime, timedelta

import pytest


async def _app(monkeypatch, tag):
    tmp = tempfile.mktemp(suffix=f"{tag}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import mcp_server, main
    importlib.reload(mcp_server); importlib.reload(main)
    return main, database


async def _archive_without_the_hour(lat, lon, lo, hi):
    """Archive responds with data for the year but not for the run's own hour.

    This is the case that silently poisoned runs: a non-empty payload reaches
    the per-run loop, so `weather_checked` got set even though no conditions
    were found.
    """
    return {"_utc_offset_h": 5.5, "1999-01-01T00:00": (20.0, 10.0)}


@pytest.mark.asyncio
async def test_a_recent_run_the_archive_lacks_stays_retryable(monkeypatch):
    main, database = await _app(monkeypatch, "wa")
    from models import Activity
    monkeypatch.setattr(main.weather, "archive_hourly", _archive_without_the_hour)

    async with database.async_session() as s:
        s.add(Activity(id=1, name="Recent", distance=3000.0, moving_time=1200,
                       start_date=datetime.utcnow() - timedelta(days=2),
                       average_speed=2.5, start_latlng=[22.77, 86.25]))
        await s.commit()
        await main._backfill_weather(s)

    async with database.async_session() as s:
        a = await s.get(Activity, 1)
        assert a.temp_c is None
        assert not a.weather_checked, (
            "a recent run with no archive row must stay retryable"
        )


@pytest.mark.asyncio
async def test_an_old_run_the_archive_lacks_is_given_up_on(monkeypatch):
    """Otherwise every backfill re-fetches runs the archive will never cover."""
    main, database = await _app(monkeypatch, "wb")
    from models import Activity
    monkeypatch.setattr(main.weather, "archive_hourly", _archive_without_the_hour)

    async with database.async_session() as s:
        s.add(Activity(id=2, name="Ancient", distance=3000.0, moving_time=1200,
                       start_date=datetime.utcnow() - timedelta(days=400),
                       average_speed=2.5, start_latlng=[22.77, 86.25]))
        await s.commit()
        await main._backfill_weather(s)

    async with database.async_session() as s:
        a = await s.get(Activity, 2)
        assert a.weather_checked, "an old run with no archive row should stop retrying"


@pytest.mark.asyncio
async def test_auto_sync_backfills_weather(monkeypatch):
    """Nothing retried the backfill before, so recent runs stayed null forever."""
    main, database = await _app(monkeypatch, "wc")
    calls = []

    async def fake_import(session):
        return {"imported": 0}

    async def fake_weather(session, force=False):
        calls.append("weather")
        return {"updated": 0}

    async def fake_wellness(session, day, refresh=False):
        return {}

    monkeypatch.setattr(main, "import_garmin_sync", fake_import)
    monkeypatch.setattr(main, "_backfill_weather", fake_weather)
    monkeypatch.setattr(main, "_wellness", fake_wellness)

    await main._auto_sync_once()

    assert "weather" in calls
