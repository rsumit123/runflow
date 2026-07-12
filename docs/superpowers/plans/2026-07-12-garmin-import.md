# Garmin Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an additive Garmin import path so new runs (with heart rate + cadence/running-dynamics) flow into RunFlow's existing analysis pipeline, alongside the untouched (now-inactive) Strava integration.

**Architecture:** A `GarminClient` wraps the synchronous `python-garminconnect` library (calls wrapped in `asyncio.to_thread`), authenticated via a token-store file (no password on the server). Pure transform functions map Garmin payloads into RunFlow's existing `Activity`/`Split`/`Stream` shapes, which then feed the existing `encode_polyline` and `compute_and_store_best_efforts` helpers. A new `POST /api/import/garmin/sync` endpoint and a "Sync from Garmin" button drive it manually.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async + aiosqlite, `python-garminconnect`, pytest + pytest-asyncio (new), React (CRA), Recharts.

**Spec:** `docs/superpowers/specs/2026-07-12-garmin-import-design.md`

**Credential note:** Tasks 1 and 12 require a live Garmin login and MUST be run locally by the user (credentials never go to a subagent or the server). Every other task is offline and fully testable without credentials.

---

## File Structure

- Create `backend/garmin_auth.py` — build an authenticated `Garmin` client from the token store.
- Create `backend/garmin_transform.py` — pure functions: Garmin payloads → RunFlow field dicts / stream arrays. No I/O, fully unit-tested.
- Create `backend/garmin_client.py` — `GarminClient`: async wrappers (`asyncio.to_thread`) around the library + running-type filter.
- Modify `backend/models.py` — new nullable columns on `Activity` and `Split`.
- Modify `backend/database.py` — idempotent column migration in `init_db`.
- Modify `backend/config.py` — `GARMIN_TOKENSTORE`.
- Modify `backend/main.py` — `_import_garmin_activity` + `POST /api/import/garmin/sync`.
- Modify `backend/requirements.txt` — add `garminconnect`.
- Create `backend/requirements-dev.txt` — `pytest`, `pytest-asyncio`.
- Create `backend/tests/` — `conftest.py`, fixtures, unit tests.
- Modify `frontend/src/api.js` + `frontend/src/pages/Import.js` — Sync-from-Garmin button.
- Modify `frontend/src/pages/ActivityDetail.js` + `frontend/src/components/SplitsTable.js` — HR/cadence/dynamics display.
- Create `GARMIN.md` — token-refresh runbook.

Transform function contract (used across tasks — names are fixed):
- `summary_to_activity_fields(summary: dict) -> dict`
- `laps_to_splits(splits_payload: dict) -> list[dict]`
- `details_to_streams(details: dict) -> dict[str, list]`
- `hr_zones(zones_payload: list) -> list[dict]`
- `running_dynamics_summary(streams: dict[str, list]) -> dict | None`
- `is_garmin_running(type_key: str | None) -> bool`

---

## Task 0: Test tooling

**Files:**
- Create: `backend/requirements-dev.txt`
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/pytest.ini`

- [ ] **Step 1: Create dev requirements**

`backend/requirements-dev.txt`:
```
pytest
pytest-asyncio
```

- [ ] **Step 2: Create pytest config**

`backend/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 3: Create empty tests package**

Create empty file `backend/tests/__init__.py`.

- [ ] **Step 4: Install and verify pytest runs**

Run (from `backend/`): `pip install -r requirements-dev.txt && pytest -q`
Expected: `no tests ran` (exit 5) — confirms pytest is installed and configured.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements-dev.txt backend/pytest.ini backend/tests/__init__.py
git commit -m "test: add pytest tooling"
```

---

## Task 1: Capture real Garmin payloads (USER-RUN, credentials)

**Files:**
- Create: `backend/tests/fixtures/garmin_summary.json`
- Create: `backend/tests/fixtures/garmin_splits.json`
- Create: `backend/tests/fixtures/garmin_details.json`
- Create: `backend/tests/fixtures/garmin_hr_zones.json`
- Create: `backend/dump_garmin_fixtures.py` (throwaway helper)

- [ ] **Step 1: Write the dump helper**

`backend/dump_garmin_fixtures.py`:
```python
"""USER-RUN: dumps real Garmin payloads to tests/fixtures/ for offline tests.
Run locally; enter your own credentials (never stored). Delete after use."""
import getpass, json, os
from garminconnect import Garmin

os.makedirs("tests/fixtures", exist_ok=True)
email = input("Garmin email: ").strip()
pw = getpass.getpass("Garmin password (hidden): ")
g = Garmin(email=email, password=pw, prompt_mfa=lambda: input("MFA code: ").strip())
g.login("/tmp/garmin_tokens")

acts = g.get_activities(0, 1)
aid = acts[0]["activityId"]
dumps = {
    "garmin_summary.json": acts[0],
    "garmin_splits.json": g.get_activity_splits(aid),
    "garmin_details.json": g.get_activity_details(aid),
    "garmin_hr_zones.json": g.get_activity_hr_in_timezones(aid),
}
for name, obj in dumps.items():
    with open(f"tests/fixtures/{name}", "w") as f:
        json.dump(obj, f, indent=2)
    print(f"wrote tests/fixtures/{name}")
print("\nInspect garmin_details.json['metricDescriptors'] for the real metric keys.")
```

- [ ] **Step 2: User runs it locally**

Run (from `backend/`, in a venv with `garminconnect`):
`python dump_garmin_fixtures.py`
Expected: four JSON files written to `tests/fixtures/`.

- [ ] **Step 3: Record the real metric keys**

Open `tests/fixtures/garmin_details.json`, read the `metricDescriptors` array, and note the `key` string for each of: latitude, longitude, timestamp, distance, speed, heart rate, cadence, ground contact time, vertical oscillation, stride length. These confirm/correct the `METRIC_KEYS` constant in Task 3. If a running-dynamics key is absent, that metric simply won't be tested/imported (expected — device-dependent).

- [ ] **Step 4: Commit the fixtures**

```bash
git add backend/tests/fixtures/*.json
git commit -m "test: capture real Garmin API fixtures"
```
(Do not commit `dump_garmin_fixtures.py`; delete it after use.)

---

## Task 2: Schema — new columns + migration

**Files:**
- Modify: `backend/models.py` (Activity, Split)
- Modify: `backend/database.py` (init_db)
- Test: `backend/tests/test_migration.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_migration.py`:
```python
import os, tempfile, asyncio
import pytest

async def _columns(engine, table):
    from sqlalchemy import text
    async with engine.begin() as conn:
        rows = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return {r[1] for r in rows.fetchall()}

@pytest.mark.asyncio
async def test_migration_adds_columns(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    # simulate an old DB: create tables WITHOUT the new columns, then migrate
    await database.init_db()
    acols = await _columns(database.engine, "activities")
    scols = await _columns(database.engine, "splits")
    assert {"source", "average_heartrate", "max_heartrate",
            "average_cadence", "hr_zones", "running_dynamics"} <= acols
    assert "average_cadence" in scols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_migration.py -q`
Expected: FAIL (columns missing).

- [ ] **Step 3: Add columns to models**

In `backend/models.py`, add to `class Activity` (after `interval_config`):
```python
    source = Column(String, default="strava", index=True)  # 'strava' | 'garmin'
    average_heartrate = Column(Float, nullable=True)
    max_heartrate = Column(Float, nullable=True)
    average_cadence = Column(Float, nullable=True)
    hr_zones = Column(JSON, nullable=True)            # [{zone, secs}, ...]
    running_dynamics = Column(JSON, nullable=True)    # {stride_length, gct, vertical_oscillation}
```
Add to `class Split` (after `average_heartrate`):
```python
    average_cadence = Column(Float, nullable=True)
```

- [ ] **Step 4: Add idempotent migration to init_db**

In `backend/database.py`, replace `init_db` with:
```python
_MIGRATIONS = {
    "activities": {
        "source": "TEXT DEFAULT 'strava'",
        "average_heartrate": "FLOAT",
        "max_heartrate": "FLOAT",
        "average_cadence": "FLOAT",
        "hr_zones": "JSON",
        "running_dynamics": "JSON",
    },
    "splits": {
        "average_cadence": "FLOAT",
    },
}


async def init_db() -> None:
    """Create all tables, then add any missing columns (idempotent)."""
    async with engine.begin() as conn:
        from models import Activity, Split, Stream  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
        for table, cols in _MIGRATIONS.items():
            existing = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            have = {r[1] for r in existing.fetchall()}
            for col, decl in cols.items():
                if col not in have:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {col} {decl}"
                    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_migration.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/database.py backend/tests/test_migration.py
git commit -m "feat: add Garmin metric columns + idempotent migration"
```

---

## Task 3: Config — token store path + metric key map

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: Add config values**

Append to `backend/config.py`:
```python
# Garmin
GARMIN_TOKENSTORE = os.getenv("GARMIN_TOKENSTORE", "/data/garmin_tokens")

# activityDetailMetrics descriptor keys → RunFlow stream types.
# Confirm/adjust against tests/fixtures/garmin_details.json['metricDescriptors'] (Task 1).
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
```

- [ ] **Step 2: Verify import**

Run (from `backend/`): `python -c "import config; print(config.GARMIN_TOKENSTORE, config.GARMIN_METRIC_KEYS['heartrate'])"`
Expected: prints the path and `directHeartRate`.

- [ ] **Step 3: Commit**

```bash
git add backend/config.py
git commit -m "feat: Garmin config (token store, metric key map)"
```

---

## Task 4: Transform functions (pure, TDD against fixtures)

**Files:**
- Create: `backend/garmin_transform.py`
- Test: `backend/tests/test_garmin_transform.py`

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_garmin_transform.py`:
```python
import json, pathlib
from datetime import datetime
import garmin_transform as gt

FIX = pathlib.Path(__file__).parent / "fixtures"
summary = json.loads((FIX / "garmin_summary.json").read_text())
splits = json.loads((FIX / "garmin_splits.json").read_text())
details = json.loads((FIX / "garmin_details.json").read_text())
zones = json.loads((FIX / "garmin_hr_zones.json").read_text())

def test_is_garmin_running():
    assert gt.is_garmin_running("track_running")
    assert gt.is_garmin_running("running")
    assert not gt.is_garmin_running("cycling")
    assert not gt.is_garmin_running(None)

def test_summary_fields():
    f = gt.summary_to_activity_fields(summary)
    assert f["id"] == summary["activityId"]
    assert f["source"] == "garmin"
    assert f["distance"] == summary["distance"]
    assert isinstance(f["start_date"], datetime)
    assert f["sport_type"] == summary["activityType"]["typeKey"]

def test_splits_nonempty():
    rows = gt.laps_to_splits(splits)
    assert len(rows) >= 1
    assert rows[0]["split_number"] == 1
    assert "average_speed" in rows[0]

def test_streams_have_core_types():
    s = gt.details_to_streams(details)
    assert "latlng" in s and len(s["latlng"]) > 10
    assert "time" in s and s["time"][0] == 0          # elapsed starts at 0
    assert len(s["latlng"]) == len(s["time"])         # index-aligned
    assert isinstance(s["latlng"][0], list) and len(s["latlng"][0]) == 2

def test_hr_zones_normalized():
    z = gt.hr_zones(zones)
    assert isinstance(z, list)
    if z:
        assert "zone" in z[0] and "secs" in z[0]
```
(If Task 1 showed HR is present, also assert `"heartrate" in gt.details_to_streams(details)`.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_garmin_transform.py -q`
Expected: FAIL (`No module named garmin_transform`).

- [ ] **Step 3: Implement the transforms**

`backend/garmin_transform.py`:
```python
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
    }


def _as_int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_garmin_transform.py -q`
Expected: PASS. If a metric key assertion fails, correct `GARMIN_METRIC_KEYS` in `config.py` to the real key from the fixture (Task 1, Step 3), then rerun.

- [ ] **Step 5: Commit**

```bash
git add backend/garmin_transform.py backend/tests/test_garmin_transform.py
git commit -m "feat: Garmin payload transforms with tests"
```

---

## Task 5: Auth + client wrapper

**Files:**
- Create: `backend/garmin_auth.py`
- Create: `backend/garmin_client.py`
- Test: `backend/tests/test_garmin_auth.py`

- [ ] **Step 1: Write the failing test (missing-token error is clear)**

`backend/tests/test_garmin_auth.py`:
```python
import pytest
import garmin_auth

def test_missing_token_raises_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(garmin_auth.config, "GARMIN_TOKENSTORE", str(tmp_path / "nope"))
    garmin_auth._client = None
    with pytest.raises(RuntimeError) as e:
        garmin_auth.get_garmin()
    assert "token" in str(e.value).lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_garmin_auth.py -q`
Expected: FAIL (`No module named garmin_auth`).

- [ ] **Step 3: Implement auth**

`backend/garmin_auth.py`:
```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_garmin_auth.py -q`
Expected: PASS.

- [ ] **Step 5: Implement the async client wrapper**

`backend/garmin_client.py`:
```python
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
```

- [ ] **Step 6: Verify import compiles**

Run: `python -c "import garmin_client; print('ok')"` (needs `garminconnect` installed — add in Task 8; if not yet installed, skip and rely on Task 8 verification).

- [ ] **Step 7: Commit**

```bash
git add backend/garmin_auth.py backend/garmin_client.py backend/tests/test_garmin_auth.py
git commit -m "feat: Garmin auth (token store) + async client wrapper"
```

---

## Task 6: Import helper + endpoint

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_garmin_import.py`

- [ ] **Step 1: Write the failing test (helper populates an Activity from fixtures, no network)**

`backend/tests/test_garmin_import.py`:
```python
import json, pathlib, tempfile
import pytest

FIX = pathlib.Path(__file__).parent / "fixtures"

@pytest.mark.asyncio
async def test_persist_garmin_activity(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Split, Stream
    import main
    importlib.reload(main)

    summary = json.loads((FIX / "garmin_summary.json").read_text())
    splits = json.loads((FIX / "garmin_splits.json").read_text())
    details = json.loads((FIX / "garmin_details.json").read_text())
    zones = json.loads((FIX / "garmin_hr_zones.json").read_text())

    async with database.async_session() as s:
        await main._persist_garmin_activity(s, summary, splits, details, zones)
        await s.commit()
        act = await s.get(Activity, summary["activityId"])
        assert act is not None
        assert act.source == "garmin"
        assert act.has_detailed_data is True
        assert act.map_summary_polyline  # derived from latlng
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_garmin_import.py -q`
Expected: FAIL (`_persist_garmin_activity` not defined).

- [ ] **Step 3: Add the helper + endpoint to main.py**

Add imports near the other local imports in `backend/main.py`:
```python
import asyncio as _asyncio
import garmin_transform as gt
from garmin_client import GarminClient

garmin = GarminClient()
```

Add the persist helper (place after `_import_detail_and_streams`):
```python
async def _persist_garmin_activity(session, summary, splits_payload, details, zones) -> None:
    """Map Garmin payloads into Activity/Split/Stream and run the analysis helpers."""
    fields = gt.summary_to_activity_fields(summary)
    activity_id = fields["id"]

    act = await session.get(Activity, activity_id)
    if act is None:
        act = Activity(id=activity_id)
        session.add(act)
    for k, v in fields.items():
        if k != "id":
            setattr(act, k, v)

    # Splits (replace existing)
    for old in (await session.execute(select(Split).where(Split.activity_id == activity_id))).scalars().all():
        await session.delete(old)
    for sd in gt.laps_to_splits(splits_payload):
        session.add(Split(activity_id=activity_id, **sd))

    # Streams (replace existing)
    for old in (await session.execute(select(Stream).where(Stream.activity_id == activity_id))).scalars().all():
        await session.delete(old)
    streams = gt.details_to_streams(details)
    for stype, data in streams.items():
        session.add(Stream(activity_id=activity_id, stream_type=stype, data=data))

    if streams.get("latlng"):
        act.map_summary_polyline = encode_polyline(streams["latlng"])
    act.hr_zones = gt.hr_zones(zones)
    act.running_dynamics = gt.running_dynamics_summary(streams)
    act.has_detailed_data = True

    try:
        await compute_and_store_best_efforts(session, activity_id)
    except Exception as exc:
        logger.warning("best efforts failed for %s: %s", activity_id, exc)
```

Add the endpoint (place near the other import endpoints):
```python
@app.post("/api/import/garmin/sync")
async def import_garmin_sync(session: AsyncSession = Depends(get_session)):
    """Incremental Garmin sync: import running activities not already stored."""
    try:
        gc = garmin
        imported = already_existed = 0
        start, page_size = 0, 20
        while True:
            try:
                acts = await gc.get_recent_running(limit=page_size, start=start)
            except Exception as exc:
                logger.error("Garmin fetch failed at start=%d: %s", start, exc)
                if start == 0:
                    raise HTTPException(status_code=502, detail=f"Garmin fetch failed: {exc}")
                break
            if not acts:
                break
            page_all_exist = True
            for summary in acts:
                aid = summary["activityId"]
                existing = await session.get(Activity, aid)
                if existing and existing.has_detailed_data:
                    already_existed += 1
                    continue
                page_all_exist = False
                splits = await gc.get_splits(aid)
                details = await gc.get_details(aid)
                try:
                    zones = await gc.get_hr_zones(aid)
                except Exception:
                    zones = []
                await _persist_garmin_activity(session, summary, splits, details, zones)
                imported += 1
                await session.commit()
            if page_all_exist:
                break
            start += page_size
            await _asyncio.sleep(1)  # be gentle with Garmin
        return {"imported": imported, "already_existed": already_existed, "skipped_non_running": 0}
    except HTTPException:
        raise
    except RuntimeError as exc:  # missing token, etc.
        raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_garmin_import.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_garmin_import.py
git commit -m "feat: Garmin import helper + /api/import/garmin/sync endpoint"
```

---

## Task 7: Frontend — Sync from Garmin button

**Files:**
- Modify: `frontend/src/pages/Import.js`

- [ ] **Step 1: Add the handler**

In `frontend/src/pages/Import.js`, near `handleSync`, add:
```javascript
  const [garminLoading, setGarminLoading] = useState(false);

  const handleGarminSync = async () => {
    setGarminLoading(true);
    setSyncStatus(null);
    try {
      const res = await api.post('/import/garmin/sync');
      setSyncStatus({
        type: 'success',
        message: `Garmin: imported ${res.data.imported} new run(s) (${res.data.already_existed} already had).`,
      });
    } catch (err) {
      setSyncStatus({
        type: 'error',
        message: err.response?.data?.detail || 'Garmin sync failed.',
      });
    } finally {
      setGarminLoading(false);
    }
  };
```

- [ ] **Step 2: Add the button**

Next to the existing Sync button in the JSX, add:
```jsx
        <button
          onClick={handleGarminSync}
          disabled={garminLoading}
          style={{ minHeight: 44, background: '#007cc3', color: '#fff',
                   border: 'none', borderRadius: 8, padding: '0 16px', cursor: 'pointer' }}
        >
          {garminLoading ? 'Syncing Garmin…' : 'Sync from Garmin'}
        </button>
```

- [ ] **Step 3: Verify in the browser**

Run the frontend (`npm start` in `frontend/`), open the Import page, confirm the "Sync from Garmin" button renders next to the Strava Sync button. (It will error until Task 8 installs the dep and a token exists — that's expected here.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Import.js
git commit -m "feat: Sync from Garmin button"
```

---

## Task 8: Dependency + deploy plumbing

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile` (verify volume/env only)
- Create: `GARMIN.md`

- [ ] **Step 1: Add the dependency**

Append `garminconnect` to `backend/requirements.txt`.

- [ ] **Step 2: Install locally and smoke-test imports**

Run (from `backend/`): `pip install -r requirements.txt && python -c "import garmin_client, garmin_auth, main; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Write the runbook**

`GARMIN.md`:
```markdown
# Garmin token runbook

RunFlow reads Garmin via a cached token store (no password on the server).

## First-time / refresh (~yearly, or if calls start 401ing)
1. Locally, in a venv with `garminconnect`:
   python -c "from garminconnect import Garmin; import getpass; \
     g=Garmin(input('email: '), getpass.getpass('pw: '), prompt_mfa=lambda: input('mfa: ')); \
     g.login('/tmp/garmin_tokens'); print('ok')"
2. Copy the token dir to the VM:
   rsync -avz /tmp/garmin_tokens/ ssh-social:/opt/runflow/data/garmin_tokens/
3. Restart the container:
   ssh ssh-social "sudo docker restart runflow-backend"

The container mounts /opt/runflow/data at /data, and GARMIN_TOKENSTORE defaults
to /data/garmin_tokens.
```

- [ ] **Step 4: Confirm the volume mount already covers the token dir**

The run command already mounts `-v /opt/runflow/data:/data`, so `/data/garmin_tokens` is persistent. No Dockerfile change needed. Verify by reading `DEPLOYMENT.md` and the Dockerfile; if `GARMIN_TOKENSTORE` needs overriding, add `-e GARMIN_TOKENSTORE=/data/garmin_tokens` to the documented `docker run` (it already defaults to that path).

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt GARMIN.md
git commit -m "chore: garminconnect dep + token runbook"
```

---

## Task 9: Frontend — HR / cadence / dynamics on activity detail

**Files:**
- Modify: `frontend/src/pages/ActivityDetail.js`
- Modify: `frontend/src/components/SplitsTable.js`

- [ ] **Step 1: Read the current detail page to match patterns**

Read `frontend/src/pages/ActivityDetail.js` to find how existing streams (e.g. the pace/velocity chart) are read from the activity payload and rendered with Recharts. Reuse that exact pattern.

- [ ] **Step 2: Add an HR chart + zones block**

Following the existing chart pattern, add a section that renders when a `heartrate` stream exists: a Recharts line of HR over distance/time, plus avg/max (`activity.average_heartrate` / `activity.max_heartrate`) and, if `activity.hr_zones` is non-empty, a simple horizontal bar of `secs` per `zone`. Guard the whole section on data presence so Strava activities (no HR) render nothing.

- [ ] **Step 3: Add a cadence chart + dynamics panel**

Add a cadence line chart when a `cadence` stream exists, and a small stats panel showing `activity.running_dynamics` (stride length, ground contact time, vertical oscillation) rendered only when `running_dynamics` is non-null.

- [ ] **Step 4: Add HR/cadence columns to splits**

In `frontend/src/components/SplitsTable.js`, add HR and cadence columns that render `split.average_heartrate` / `split.average_cadence` when present (dash otherwise), so Strava splits still render cleanly.

- [ ] **Step 5: Verify in the browser**

With a Garmin activity imported (after Task 10), open its detail page and confirm: HR chart + zones, cadence chart, dynamics panel, and HR/cadence split columns all render; open a Strava activity and confirm none of the new sections appear (no empty boxes).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ActivityDetail.js frontend/src/components/SplitsTable.js
git commit -m "feat: show HR, cadence, and running dynamics on activity detail"
```

---

## Task 10: End-to-end verification (USER/VM, credentials)

**Files:** none (verification)

- [ ] **Step 1: Put the token on the VM**

Follow `GARMIN.md` steps 1–2 to generate `/tmp/garmin_tokens` locally and rsync it to `/opt/runflow/data/garmin_tokens/`.

- [ ] **Step 2: Deploy the branch to the VM**

Per `DEPLOYMENT.md`: on the VM, `cd /opt/runflow && git fetch && git checkout garmin-import && git pull`, then rebuild:
`cd backend && sudo docker build -t runflow-backend . && sudo docker rm -f runflow-backend && <the documented docker run command>`.

- [ ] **Step 3: Trigger a sync**

Run: `curl -s -X POST https://runflow-api.skdev.one/api/import/garmin/sync`
Expected: `{"imported": 3, "already_existed": 0, "skipped_non_running": 0}` (3 = current Garmin runs).

- [ ] **Step 4: Verify idempotency**

Run the same curl again.
Expected: `{"imported": 0, "already_existed": 3, ...}` — no duplicates, stops after one page.

- [ ] **Step 5: Verify in the UI**

Open `https://runflow.skdev.one`: the 3 Garmin runs appear on the dashboard; open one and confirm route map, splits (with HR/cadence), best efforts, HR chart + zones, cadence chart, and dynamics all render; confirm a Strava run still renders normally with none of the new sections.

- [ ] **Step 6: Merge**

Use `superpowers:finishing-a-development-branch` to merge `garmin-import` to `main` and deploy `main`.

---

## Self-Review Notes

- **Spec coverage:** auth/token (Task 5,8), client (Task 5), transforms/mapping incl. HR+zones+cadence+dynamics (Task 4), endpoint+incremental dedup (Task 6), schema+migration (Task 2), frontend button (Task 7) + detail display (Task 9), deps/deploy/runbook (Task 8,10), graceful degradation (present-only metrics in `details_to_streams`/`running_dynamics_summary`), error handling (endpoint 400/502 + zones try/except). All covered.
- **Credential-gated steps** (1, 10) are explicitly user/VM-run; all logic tasks are offline-testable against fixtures.
- **Type consistency:** transform function names and the `_persist_garmin_activity(session, summary, splits_payload, details, zones)` signature are used identically in Task 6's test and endpoint.
```
