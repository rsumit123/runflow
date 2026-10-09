# RunFlow MCP Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose RunFlow to the Claude app as a read + trigger-sync remote MCP server, mounted inside the existing FastAPI app.

**Architecture:** Two new modules. `mcp_views.py` is pure presentation — DB rows in, compact text out, no I/O, unit-tested in isolation like `fitness_model.py`. `mcp_server.py` holds the `MCPServer` instance and eight tools, each opening its own `async_session()`. `main.py` gains four lines: an import, a lifespan wrap, and a conditional mount whose path contains the shared secret. Design: `docs/superpowers/specs/2026-10-09-mcp-connector-design.md`.

**Tech Stack:** Python 3.13, FastAPI/Starlette, `mcp` SDK (Streamable HTTP transport), SQLAlchemy async, pytest + pytest-asyncio.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/mcp_views.py` | **Create.** Pure functions: split tables, cardiac drift, zone shares, pause detection, stream downsampling, heavy-field stripping, text formatting. No DB, no network. |
| `backend/mcp_server.py` | **Create.** `MCPServer` instance, the eight `@mcp.tool()` definitions, the GET-route index for `call_api`, and the exported ASGI app. |
| `backend/config.py` | **Modify.** Add `MCP_SECRET`. |
| `backend/requirements.txt` | **Modify.** Add `mcp`. |
| `backend/main.py` | **Modify.** Import the ASGI app, wrap the lifespan in `session_manager.run()`, conditionally mount. |
| `backend/Dockerfile` | **Modify.** uvicorn gains `--proxy-headers --forwarded-allow-ips=*`. |
| `backend/tests/test_mcp_views.py` | **Create.** Pure unit tests over synthetic streams. |
| `backend/tests/test_mcp_tools.py` | **Create.** Tool tests against a temp SQLite DB. |
| `DEPLOYMENT.md`, `IDEAS.md` | **Modify.** Deploy steps, connector URL, nginx allowlist block. |

All `pytest` commands run from `backend/` using `./venv/bin/python -m pytest`.

**Note on the pre-existing failure:** `tests/test_run_chat.py::test_tools_over_real_db` fails on `main` before any of this work (verified by stashing). It is unrelated. "All tests pass" below means 1 failed, N passed with that being the one failure.

---

## Task 1: Dependency and secret

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/config.py`

- [ ] **Step 1: Add the SDK to requirements**

Append to `backend/requirements.txt`:

```
mcp
```

- [ ] **Step 2: Install it into the venv**

Run: `cd backend && ./venv/bin/pip install mcp`
Expected: installs `mcp` and its dependencies.

- [ ] **Step 3: Verify the import surface matches the spec**

Run:
```bash
cd backend && ./venv/bin/python -c "
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
m = MCPServer('probe')
app = m.streamable_http_app(transport_security=TransportSecuritySettings(allowed_hosts=['x.test']))
print('ok', type(app).__name__, hasattr(m, 'session_manager'))
"
```
Expected: `ok Starlette True`

If the import path or method name differs, STOP and reconcile against
https://py.sdk.modelcontextprotocol.io/run/asgi/ before continuing — every later task
depends on these three names.

- [ ] **Step 4: Add the secret to config**

In `backend/config.py`, alongside the other `os.getenv` reads, add:

```python
# MCP connector — the secret lives in the mount path, because Claude app custom
# connectors accept only authless or OAuth (no bearer-token field). Unset means
# the MCP server is not mounted at all, so a missing secret fails closed.
MCP_SECRET = os.getenv("MCP_SECRET", "").strip()
```

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/config.py
git commit -m "chore(mcp): add the mcp SDK and the MCP_SECRET setting"
```

---

## Task 2: Pause detection and moving-time splits

The pace of a split must be computed on moving time. A run with an 11-minute standing
pause (2026-09-06 is the real case) otherwise reports a 39:42/km split.

**Files:**
- Create: `backend/mcp_views.py`
- Test: `backend/tests/test_mcp_views.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_mcp_views.py`:

```python
"""Pure-function tests for the MCP presentation layer."""
import mcp_views as mv


def test_find_pauses_detects_a_gap_with_no_distance_gained():
    time = [0, 1, 2, 120, 121]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]
    hr = [150.0, 160.0, 190.0, 148.0, 150.0]

    pauses = mv.find_pauses(time, dist, hr)

    assert len(pauses) == 1
    assert pauses[0]["at_km"] == 0.006
    assert pauses[0]["seconds"] == 118
    assert pauses[0]["hr_in"] == 190.0
    assert pauses[0]["hr_out"] == 148.0


def test_find_pauses_ignores_a_gap_where_distance_advanced():
    # A sparse sample is not a pause if the runner covered ground across it.
    time = [0, 1, 2, 120]
    dist = [0.0, 3.0, 6.0, 400.0]

    assert mv.find_pauses(time, dist, None) == []


def test_find_pauses_ignores_short_gaps():
    time = [0, 1, 2, 5, 6]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]

    assert mv.find_pauses(time, dist, None) == []


def test_moving_time_axis_excludes_paused_seconds():
    time = [0, 1, 2, 120, 121]
    dist = [0.0, 3.0, 6.0, 6.0, 9.0]

    moving = mv.moving_time_axis(time, dist)

    assert moving == [0, 1, 2, 3, 4]


def test_splits_use_moving_time_not_wall_clock():
    # 1000 m at a steady 2 m/s = 500 s, with a 600 s pause at the 600 m mark.
    time, dist, hr = [], [], []
    t = 0
    for metre in range(0, 1001):
        if metre == 600:
            t += 600  # the pause
        time.append(t)
        dist.append(float(metre))
        hr.append(180.0)
        t += 1  # 1 m/s after the first sample

    splits = mv.split_table(dist, time, hr, metres=500)

    assert len(splits) == 2
    # 500 m at 1 m/s = 500 s/km, and the pause must not leak into split 2.
    assert splits[0]["pace_sec_per_km"] == 1000
    assert splits[1]["pace_sec_per_km"] == 1000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_views'`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/mcp_views.py`:

```python
"""Compact, pre-digested views of a run, for the MCP tool surface.

Pure functions: streams and rows in, small dicts and strings out. No DB, no
network, no formatting of API responses — so every number here is unit-testable.

The reason this module exists: the REST payloads carry encoded polylines and
~1,240-sample streams per run. Handing those to a model spends the context
window on data and buries the three numbers that answer the question.
"""
from __future__ import annotations

from typing import Any, Optional

# A gap longer than this with no distance gained is the watch auto-pausing,
# not sparse sampling.
PAUSE_GAP_SEC = 3
# Distance (m) the runner must cover across a gap for it to count as movement.
MOVED_EPSILON_M = 0.5


def find_pauses(
    time: Optional[list[Optional[int]]],
    dist: Optional[list[Optional[float]]],
    hr: Optional[list[Optional[float]]] = None,
) -> list[dict[str, Any]]:
    """Where the run stopped: location, duration, and HR either side.

    HR entering and leaving a pause is the useful part — a drop from 191 to 148
    says the heart recovered while the legs did not, which is why the segment
    after a pause is often faster than anything before it.
    """
    if not time or not dist or len(time) != len(dist):
        return []
    out = []
    for i in range(len(time) - 1):
        t0, t1 = time[i], time[i + 1]
        d0, d1 = dist[i], dist[i + 1]
        if t0 is None or t1 is None or d0 is None or d1 is None:
            continue
        gap = t1 - t0
        if gap <= PAUSE_GAP_SEC or (d1 - d0) > MOVED_EPSILON_M:
            continue
        out.append({
            "at_km": round(d0 / 1000.0, 3),
            "seconds": gap,
            "hr_in": hr[i] if hr and i < len(hr) else None,
            "hr_out": hr[i + 1] if hr and i + 1 < len(hr) else None,
        })
    return out


def moving_time_axis(
    time: Optional[list[Optional[int]]],
    dist: Optional[list[Optional[float]]],
) -> list[int]:
    """Rebuild the time axis with paused seconds removed."""
    if not time or not dist:
        return []
    axis, acc = [], 0
    for i in range(len(time)):
        if i:
            t0, t1 = time[i - 1], time[i]
            d0, d1 = dist[i - 1], dist[i]
            if t0 is None or t1 is None:
                acc += 1
            else:
                gap = t1 - t0
                moved = ((d1 or 0.0) - (d0 or 0.0)) > MOVED_EPSILON_M
                acc += gap if (gap <= PAUSE_GAP_SEC or moved) else 1
        axis.append(acc)
    return axis


def split_table(
    dist: Optional[list[Optional[float]]],
    time: Optional[list[Optional[int]]],
    hr: Optional[list[Optional[float]]],
    metres: int = 500,
) -> list[dict[str, Any]]:
    """Per-`metres` splits on moving time, with mean and peak HR per split."""
    if not dist or not time:
        return []
    axis = moving_time_axis(time, dist)
    marks, nxt = [], metres
    for i, m in enumerate(dist):
        if m is not None and m >= nxt:
            marks.append((nxt, axis[i], i))
            nxt += metres

    rows, prev_t, prev_i = [], 0, 0
    for end_m, t, i in marks:
        seg = [h for h in (hr or [])[prev_i:i] if h]
        elapsed = t - prev_t
        rows.append({
            "from_m": end_m - metres,
            "to_m": end_m,
            "pace_sec_per_km": round(elapsed / (metres / 1000.0)),
            "hr_avg": round(sum(seg) / len(seg), 1) if seg else None,
            "hr_peak": max(seg) if seg else None,
        })
        prev_t, prev_i = t, i
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_views.py backend/tests/test_mcp_views.py
git commit -m "feat(mcp): moving-time splits and pause detection"
```

---

## Task 3: Drift, zone shares, time-to-zone

**Files:**
- Modify: `backend/mcp_views.py`
- Test: `backend/tests/test_mcp_views.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_views.py`:

```python
def test_cardiac_drift_is_second_half_mean_minus_first_half_mean():
    hr = [150.0] * 10 + [170.0] * 10

    assert mv.cardiac_drift(hr) == 20.0


def test_cardiac_drift_is_none_without_enough_samples():
    assert mv.cardiac_drift([150.0]) is None
    assert mv.cardiac_drift(None) is None


def test_seconds_to_cross_returns_first_crossing():
    hr = [140.0, 150.0, 170.0, 195.0]
    time = [0, 10, 20, 30]

    assert mv.seconds_to_cross(hr, time, 168) == 20
    assert mv.seconds_to_cross(hr, time, 189) == 30


def test_seconds_to_cross_returns_none_when_never_crossed():
    assert mv.seconds_to_cross([140.0, 150.0], [0, 1], 189) is None


def test_zone_shares_are_percentages_of_recorded_time():
    zones = [
        {"zone": 1, "secs": 0.0, "low_bpm": 105},
        {"zone": 2, "secs": 25.0, "low_bpm": 126},
        {"zone": 3, "secs": 25.0, "low_bpm": 147},
        {"zone": 4, "secs": 25.0, "low_bpm": 168},
        {"zone": 5, "secs": 25.0, "low_bpm": 189},
    ]

    shares = mv.zone_shares(zones)

    assert shares[5] == 25.0
    assert shares[1] == 0.0
    assert sum(shares.values()) == 100.0


def test_zone_shares_handles_missing_zones():
    assert mv.zone_shares(None) == {}
    assert mv.zone_shares([]) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: FAIL — `AttributeError: module 'mcp_views' has no attribute 'cardiac_drift'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_views.py`:

```python
def cardiac_drift(hr: Optional[list[Optional[float]]]) -> Optional[float]:
    """Second-half mean HR minus first-half mean HR.

    Read this alongside the zone shares, never alone: a run that finally starts
    easy pushes the first-half mean down and so reports *higher* drift than a
    run that was hard from the gun. The metric punishes good pacing.
    """
    vals = [h for h in (hr or []) if h]
    if len(vals) < 2:
        return None
    mid = len(vals) // 2
    first, second = vals[:mid], vals[mid:]
    return round(sum(second) / len(second) - sum(first) / len(first), 1)


def seconds_to_cross(
    hr: Optional[list[Optional[float]]],
    time: Optional[list[Optional[int]]],
    bpm: int,
) -> Optional[int]:
    """When HR first reached `bpm` — how long the aerobic part of the run lasted."""
    if not hr or not time:
        return None
    for i, h in enumerate(hr):
        if h and h >= bpm and i < len(time) and time[i] is not None:
            return time[i]
    return None


def zone_shares(zones: Optional[list[dict[str, Any]]]) -> dict[int, float]:
    """Percentage of recorded time in each HR zone."""
    rows = zones or []
    total = sum((z.get("secs") or 0.0) for z in rows)
    if not total:
        return {}
    return {
        int(z["zone"]): round(100.0 * (z.get("secs") or 0.0) / total, 1)
        for z in rows
        if z.get("zone") is not None
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_views.py backend/tests/test_mcp_views.py
git commit -m "feat(mcp): cardiac drift, zone shares, time-to-zone"
```

---

## Task 4: Payload pruning for the escape hatch

**Files:**
- Modify: `backend/mcp_views.py`
- Test: `backend/tests/test_mcp_views.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_views.py`:

```python
def test_strip_heavy_removes_polylines_and_streams():
    payload = {
        "activities": [
            {"id": 1, "map_summary_polyline": "sm_jCajmmOVE", "distance": 3000.0},
        ],
        "streams": [{"stream_type": "heartrate", "data": [150.0] * 1200}],
    }

    pruned = mv.strip_heavy(payload)

    assert "map_summary_polyline" not in pruned["activities"][0]
    assert pruned["activities"][0]["distance"] == 3000.0
    assert "streams" not in pruned


def test_strip_heavy_leaves_light_payloads_alone():
    payload = {"best_1km_split": {"time": 276}}

    assert mv.strip_heavy(payload) == payload


def test_downsample_keeps_first_and_last_and_reports_true_length():
    data = list(range(1000))

    out = mv.downsample(data, max_points=10)

    assert out["original_samples"] == 1000
    assert len(out["data"]) <= 10
    assert out["data"][0] == 0
    assert out["data"][-1] == 999


def test_downsample_leaves_short_series_untouched():
    out = mv.downsample([1, 2, 3], max_points=10)

    assert out["data"] == [1, 2, 3]
    assert out["original_samples"] == 3


def test_cap_text_truncates_with_an_explicit_marker():
    out = mv.cap_text("x" * 100, limit=50)

    assert len(out) <= 120  # the marker adds a line
    assert "truncated" in out.lower()


def test_cap_text_leaves_short_text_alone():
    assert mv.cap_text("short", limit=50) == "short"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: FAIL — `AttributeError: module 'mcp_views' has no attribute 'strip_heavy'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_views.py`:

```python
# Keys whose values are large and almost never what was being asked for.
HEAVY_KEYS = ("map_summary_polyline", "streams", "polyline")
MAX_STREAM_POINTS = 200
MAX_RESPONSE_CHARS = 25_000


def strip_heavy(value: Any) -> Any:
    """Recursively drop encoded polylines and raw streams from an API payload."""
    if isinstance(value, dict):
        return {
            k: strip_heavy(v)
            for k, v in value.items()
            if k not in HEAVY_KEYS
        }
    if isinstance(value, list):
        return [strip_heavy(v) for v in value]
    return value


def downsample(data: list[Any], max_points: int = MAX_STREAM_POINTS) -> dict[str, Any]:
    """Thin a stream to at most `max_points`, keeping the first and last sample.

    `original_samples` is returned so a thinned series is never mistaken for the
    whole thing.
    """
    n = len(data)
    if n <= max_points:
        return {"original_samples": n, "data": data}
    step = max(1, n // (max_points - 1))
    thinned = data[::step][: max_points - 1]
    if thinned and thinned[-1] != data[-1]:
        thinned.append(data[-1])
    return {"original_samples": n, "data": thinned}


def cap_text(text: str, limit: int = MAX_RESPONSE_CHARS) -> str:
    """Hard cap on tool output, truncating visibly.

    A silent truncation that reads as a complete answer is the failure being
    prevented here.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[truncated at {limit} characters of {len(text)}]"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_views.py -q`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_views.py backend/tests/test_mcp_views.py
git commit -m "feat(mcp): payload pruning, stream downsampling, output cap"
```

---

## Task 5: Server scaffold, mount, and lifespan

No tool logic yet — this task proves the transport works end to end.

**Files:**
- Create: `backend/mcp_server.py`
- Modify: `backend/main.py` (lifespan at `:92`, app at `:100`)
- Test: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_mcp_tools.py`:

```python
"""Tool-level tests against a temp SQLite DB."""
import tempfile
import pytest


@pytest.mark.asyncio
async def test_mcp_mounts_only_when_a_secret_is_set(monkeypatch):
    """No MCP_SECRET must mean no mounted route — fail closed, not fail open."""
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import main
    importlib.reload(main)

    assert not any("/mcp/" in getattr(r, "path", "") for r in main.app.routes)


@pytest.mark.asyncio
async def test_mcp_mounts_under_the_secret_path(monkeypatch):
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import main
    importlib.reload(main)

    assert any(getattr(r, "path", "") == "/mcp/testsecret" for r in main.app.routes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — the second test fails, no route matches `/mcp/testsecret`

- [ ] **Step 3: Write the minimal implementation**

Create `backend/mcp_server.py`:

```python
"""The RunFlow MCP server — RunFlow as a Claude app connector.

Mounted into the FastAPI app at a path containing a shared secret, because
Claude app custom connectors accept only authless or OAuth servers: there is no
field for a bearer token or a custom header. The mount path *is* the credential.

Scope is read + trigger-sync. Nothing here writes training data.
"""
from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

import config

mcp = MCPServer("RunFlow")

# The transport rejects any non-localhost Host header with 421 Misdirected
# Request unless the hostname is allowlisted here. nginx forwards the public
# hostname through, so both the bare form and the any-port form are needed.
_ALLOWED_HOSTS = [
    "runflow-api.skdev.one",
    "runflow-api.skdev.one:*",
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
]

asgi_app = mcp.streamable_http_app(
    transport_security=TransportSecuritySettings(allowed_hosts=_ALLOWED_HOSTS),
)

MOUNT_PATH = f"/mcp/{config.MCP_SECRET}" if config.MCP_SECRET else None
```

In `backend/main.py`, add the import next to the other local imports:

```python
import mcp_server
```

Wrap the existing lifespan body (currently `main.py:92-98`) so it becomes:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = _asyncio.create_task(_auto_sync_loop())
    # A mounted sub-app's lifespan never runs, so the MCP session manager has to
    # be entered by the host app — without this the first MCP request raises
    # "Task group is not initialized".
    async with mcp_server.mcp.session_manager.run():
        yield
    task.cancel()
    await strava.close()
```

And after the CORS middleware block, add the mount:

```python
# MCP connector. No secret => no mount => 404, rather than an open endpoint.
if mcp_server.MOUNT_PATH:
    app.mount(mcp_server.MOUNT_PATH, mcp_server.asgi_app)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: `2 passed`

- [ ] **Step 5: Verify the whole suite still passes**

Run: `cd backend && ./venv/bin/python -m pytest -q`
Expected: `1 failed, N passed` — the one failure being the pre-existing
`test_run_chat.py::test_tools_over_real_db`.

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server.py backend/main.py backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): mount the MCP server behind a secret path"
```

---

## Task 6: `list_recent_runs` and `get_run_detail`

**Files:**
- Modify: `backend/mcp_server.py`
- Test: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_tools.py`:

```python
from datetime import datetime, timedelta


async def _seed(monkeypatch, tmp_suffix="a"):
    """Temp DB with one paused run and one clean run, both with HR streams."""
    tmp = tempfile.mktemp(suffix=f"{tmp_suffix}.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import Activity, Stream
    import main
    importlib.reload(main)

    now = datetime(2026, 10, 9, 6, 0, 0)
    async with database.async_session() as s:
        s.add(Activity(
            id=1, name="Clean run", distance=3500.0, moving_time=1450,
            elapsed_time=1450, start_date=now - timedelta(days=1),
            average_speed=3500.0 / 1450, average_heartrate=182.0,
            max_heartrate=195.0, average_cadence=156.0, source="garmin",
            aerobic_te=4.2, training_effect_label="VO2MAX",
            temp_c=26.0, dew_point_c=24.0, heat_penalty_sec=22.0,
            hr_zones=[{"zone": z, "secs": 290.0, "low_bpm": b}
                      for z, b in [(1, 105), (2, 126), (3, 147), (4, 168), (5, 189)]],
        ))
        s.add(Stream(activity_id=1, stream_type="time", data=list(range(1451))))
        s.add(Stream(activity_id=1, stream_type="distance",
                     data=[i * (3500.0 / 1450) for i in range(1451)]))
        s.add(Stream(activity_id=1, stream_type="heartrate",
                     data=[150.0] * 725 + [195.0] * 726))

        # A fragmented run: 600 s standing still at the 1 km mark.
        t, time_s, dist_s = 0, [], []
        for metre in range(0, 2001):
            if metre == 1000:
                t += 600
            time_s.append(t); dist_s.append(float(metre)); t += 1
        s.add(Activity(
            id=2, name="Paused run", distance=2000.0, moving_time=2000,
            elapsed_time=2002, start_date=now - timedelta(days=3),
            average_speed=1.0, average_heartrate=175.0, max_heartrate=202.0,
            source="garmin",
        ))
        s.add(Stream(activity_id=2, stream_type="time", data=time_s))
        s.add(Stream(activity_id=2, stream_type="distance", data=dist_s))
        s.add(Stream(activity_id=2, stream_type="heartrate", data=[180.0] * 2001))
        await s.commit()
    return main


@pytest.mark.asyncio
async def test_list_recent_runs_reports_pace_hr_and_fragmentation(monkeypatch):
    main = await _seed(monkeypatch, "b")
    import mcp_server

    out = await mcp_server.list_recent_runs(limit=5)

    assert "Clean run" in out or "3.50" in out
    assert "182" in out            # avg HR
    assert "fragmented" in out.lower()  # the paused run is flagged


@pytest.mark.asyncio
async def test_list_recent_runs_says_so_when_there_are_no_runs(monkeypatch):
    tmp = tempfile.mktemp(suffix="c.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    import main, mcp_server
    importlib.reload(main)

    out = await mcp_server.list_recent_runs(limit=5)

    assert "no runs" in out.lower()


@pytest.mark.asyncio
async def test_get_run_detail_includes_splits_drift_and_zones(monkeypatch):
    main = await _seed(monkeypatch, "d")
    import mcp_server

    out = await mcp_server.get_run_detail(run_id=1)

    assert "split" in out.lower()
    assert "drift" in out.lower()
    assert "+45" in out or "45.0" in out   # 150 -> 195 across the halves
    assert "Z5" in out


@pytest.mark.asyncio
async def test_get_run_detail_lists_the_pauses(monkeypatch):
    main = await _seed(monkeypatch, "e")
    import mcp_server

    out = await mcp_server.get_run_detail(run_id=2)

    assert "pause" in out.lower()
    assert "10m00s" in out or "600" in out


@pytest.mark.asyncio
async def test_get_run_detail_on_a_missing_run_is_readable(monkeypatch):
    main = await _seed(monkeypatch, "f")
    import mcp_server

    out = await mcp_server.get_run_detail(run_id=9999)

    assert "not found" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute 'list_recent_runs'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_server.py`:

```python
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

import mcp_views as mv
from database import async_session
from models import Activity, Stream

logger = logging.getLogger(__name__)


def _fmt_pace(sec_per_km: Optional[float]) -> str:
    if not sec_per_km:
        return "—"
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}/km"


def _fmt_dur(secs: Optional[float]) -> str:
    if secs is None:
        return "—"
    return f"{int(secs // 60)}m{int(secs % 60):02d}s"


async def _streams_for(session, activity_id: int) -> dict[str, list]:
    rows = (await session.execute(
        select(Stream).where(Stream.activity_id == activity_id)
    )).scalars().all()
    return {r.stream_type: r.data for r in rows}


@mcp.tool()
async def list_recent_runs(limit: int = 10, since: Optional[str] = None) -> str:
    """Recent runs, newest first, as a compact table.

    Each row: id, date, distance, pace, average and max HR, share of time in
    Zone 5, aerobic Training Effect, the heat penalty the conditions imposed,
    and whether the run was fragmented (stopped and restarted).

    Start here for any question about recent training. Use the returned ids with
    get_run_detail or compare_runs.

    Args:
        limit: how many runs to return (1-50).
        since: optional ISO date (YYYY-MM-DD); only runs on or after it.
    """
    try:
        limit = max(1, min(50, limit))
        async with async_session() as session:
            q = select(Activity).order_by(Activity.start_date.desc())
            if since:
                try:
                    q = q.where(Activity.start_date >= datetime.fromisoformat(since))
                except ValueError:
                    return f"'{since}' is not an ISO date — use YYYY-MM-DD."
            acts = (await session.execute(q.limit(limit))).scalars().all()
            if not acts:
                return "No runs found for that range."

            lines = [
                "id | date | dist | pace | avgHR | maxHR | Z5% | TE | heat | notes"
            ]
            for a in acts:
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if (km and a.moving_time) else None
                shares = mv.zone_shares(a.hr_zones)
                st = await _streams_for(session, a.id)
                pauses = mv.find_pauses(st.get("time"), st.get("distance"))
                notes = f"fragmented ({len(pauses)} pauses)" if pauses else ""
                lines.append(
                    f"{a.id} | {a.start_date:%Y-%m-%d} | {km:.2f}km | "
                    f"{_fmt_pace(pace)} | {a.average_heartrate or '—'} | "
                    f"{a.max_heartrate or '—'} | "
                    f"{shares.get(5, '—')} | "
                    f"{a.aerobic_te or '—'} {a.training_effect_label or ''} | "
                    f"{a.heat_penalty_sec or '—'}s/km | {notes}"
                )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001 — a raising tool gives Claude nothing
        logger.exception("list_recent_runs failed")
        return f"Could not read recent runs: {exc}"


@mcp.tool()
async def get_run_detail(run_id: int) -> str:
    """Full analysis of one run.

    Returns 500 m splits computed on moving time (so pauses do not distort the
    pace), mean and peak HR per split, cardiac drift, how long it took to reach
    Zone 4 and Zone 5, the zone breakdown, every pause with the HR going in and
    coming out, running dynamics, and the weather with the cool-day equivalent
    pace.

    Read cardiac drift alongside the zone shares, never alone: a run that starts
    genuinely easy reports higher drift than one that was hard throughout,
    because the first-half mean is lower. High drift plus falling Z5 share is
    improvement, not regression.

    Args:
        run_id: the activity id, from list_recent_runs.
    """
    try:
        async with async_session() as session:
            a = await session.get(Activity, run_id)
            if a is None:
                return f"Run {run_id} not found."
            st = await _streams_for(session, run_id)
            hr, time, dist = st.get("heartrate"), st.get("time"), st.get("distance")

            km = (a.distance or 0) / 1000.0
            pace = (a.moving_time / km) if (km and a.moving_time) else None
            out = [
                f"{a.name or 'Run'} — {a.start_date:%Y-%m-%d %H:%M}",
                f"{km:.2f} km in {_fmt_dur(a.moving_time)} at {_fmt_pace(pace)}",
                f"HR avg {a.average_heartrate or '—'} / max {a.max_heartrate or '—'}"
                f" · cadence {a.average_cadence and round(a.average_cadence, 1) or '—'}",
                f"Training Effect {a.aerobic_te or '—'} aerobic /"
                f" {a.anaerobic_te or '—'} anaerobic {a.training_effect_label or ''}",
            ]

            shares = mv.zone_shares(a.hr_zones)
            if shares:
                out.append("Zones: " + "  ".join(
                    f"Z{z} {shares[z]}%" for z in sorted(shares)
                ))

            drift = mv.cardiac_drift(hr)
            if drift is not None:
                out.append(f"Cardiac drift: {drift:+.1f} bpm (first half vs second)")
            z4 = mv.seconds_to_cross(hr, time, 168)
            z5 = mv.seconds_to_cross(hr, time, 189)
            out.append(
                f"Crossed Z4 at {_fmt_dur(z4)}, Z5 at {_fmt_dur(z5)}"
                if (z4 or z5) else "Never left Z3."
            )

            splits = mv.split_table(dist, time, hr)
            if splits:
                out.append("")
                out.append("500 m splits (moving time):")
                for s in splits:
                    out.append(
                        f"  {s['from_m']:>4}-{s['to_m']:<4}m  "
                        f"{_fmt_pace(s['pace_sec_per_km'])}  "
                        f"HR {s['hr_avg'] or '—'} (peak {s['hr_peak'] or '—'})"
                    )

            pauses = mv.find_pauses(time, dist, hr)
            if pauses:
                out.append("")
                out.append(f"Stopped {len(pauses)} time(s) — this was not a continuous run:")
                for p in pauses:
                    out.append(
                        f"  at {p['at_km']:.2f} km — paused {_fmt_dur(p['seconds'])}"
                        f" (HR {p['hr_in'] or '—'} in, {p['hr_out'] or '—'} out)"
                    )

            rd = a.running_dynamics or {}
            if rd:
                out.append("")
                out.append(
                    f"Dynamics: stride {rd.get('stride_length')} cm · "
                    f"ground contact {rd.get('ground_contact_time')} ms · "
                    f"vertical oscillation {rd.get('vertical_oscillation')} cm"
                )

            if a.dew_point_c is not None:
                out.append("")
                out.append(
                    f"Conditions: {a.temp_c}°C, dew point {a.dew_point_c}°C"
                    f" (index {a.heat_index}) — cost ~{a.heat_penalty_sec} s/km;"
                    f" cool-day equivalent {_fmt_pace(a.normalized_pace_sec)}"
                )
            return mv.cap_text("\n".join(out))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_run_detail failed")
        return f"Could not analyse run {run_id}: {exc}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): list_recent_runs and get_run_detail tools"
```

---

## Task 7: `compare_runs`, `get_recovery`, `get_records`, `get_training_context`

**Files:**
- Modify: `backend/mcp_server.py`
- Test: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_compare_runs_puts_runs_side_by_side(monkeypatch):
    main = await _seed(monkeypatch, "g")
    import mcp_server

    out = await mcp_server.compare_runs(run_ids=[1, 2])

    assert "1" in out and "2" in out
    assert "Z5" in out or "drift" in out.lower()


@pytest.mark.asyncio
async def test_compare_runs_rejects_too_many_ids(monkeypatch):
    main = await _seed(monkeypatch, "h")
    import mcp_server

    out = await mcp_server.compare_runs(run_ids=[1, 2, 3, 4, 5, 6])

    assert "at most 5" in out.lower() or "5 runs" in out.lower()


@pytest.mark.asyncio
async def test_get_recovery_carries_the_post_run_caveat(monkeypatch):
    tmp = tempfile.mktemp(suffix="i.db")
    monkeypatch.setenv("DB_PATH", tmp)
    monkeypatch.setenv("MCP_SECRET", "testsecret")
    import importlib, config, database
    importlib.reload(config); importlib.reload(database)
    await database.init_db()
    from models import DailyWellness
    import main, mcp_server
    importlib.reload(main)

    async with database.async_session() as s:
        s.add(DailyWellness(date="2026-10-08", readiness_score=78,
                            readiness_level="HIGH", sleep_hours=7.1,
                            sleep_score=80, body_battery_peak=88,
                            hrv_last_night=52, hrv_status="BALANCED",
                            resting_hr=52))
        await s.commit()

    out = await mcp_server.get_recovery(days=7)

    assert "78" in out
    assert "after the run" in out.lower() or "post-run" in out.lower()


@pytest.mark.asyncio
async def test_get_records_and_training_context_do_not_raise(monkeypatch):
    main = await _seed(monkeypatch, "j")
    import mcp_server

    recs = await mcp_server.get_records()
    ctx = await mcp_server.get_training_context()

    assert isinstance(recs, str) and recs
    assert isinstance(ctx, str) and ctx
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute 'compare_runs'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_server.py`:

```python
@mcp.tool()
async def compare_runs(run_ids: list[int]) -> str:
    """Two to five runs side by side on the metrics that matter.

    Distance, pace, average HR, Zone 5 share, cardiac drift, Training Effect,
    dew point and weather-normalized pace. Use this to answer "am I improving?"
    — comparing a fragmented run against a continuous one is misleading, so the
    pause count is shown too.

    Args:
        run_ids: 2-5 activity ids from list_recent_runs.
    """
    try:
        if len(run_ids) > 5:
            return "Compare at most 5 runs at a time."
        if len(run_ids) < 2:
            return "Give at least 2 run ids to compare."
        async with async_session() as session:
            lines = ["id | date | dist | pace | avgHR | Z5% | drift | TE | dew | norm pace | pauses"]
            for rid in run_ids:
                a = await session.get(Activity, rid)
                if a is None:
                    lines.append(f"{rid} | not found")
                    continue
                st = await _streams_for(session, rid)
                km = (a.distance or 0) / 1000.0
                pace = (a.moving_time / km) if (km and a.moving_time) else None
                shares = mv.zone_shares(a.hr_zones)
                drift = mv.cardiac_drift(st.get("heartrate"))
                pauses = mv.find_pauses(st.get("time"), st.get("distance"))
                lines.append(
                    f"{a.id} | {a.start_date:%Y-%m-%d} | {km:.2f}km | "
                    f"{_fmt_pace(pace)} | {a.average_heartrate or '—'} | "
                    f"{shares.get(5, '—')} | "
                    f"{f'{drift:+.1f}' if drift is not None else '—'} | "
                    f"{a.aerobic_te or '—'} | {a.dew_point_c or '—'}°C | "
                    f"{_fmt_pace(a.normalized_pace_sec)} | {len(pauses)}"
                )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("compare_runs failed")
        return f"Could not compare those runs: {exc}"


@mcp.tool()
async def get_recovery(days: int = 14) -> str:
    """Recovery markers per day: readiness, sleep, body battery, HRV, resting HR.

    Resting HR trend is the most reliable single signal of whether training is
    being absorbed; HRV status and body battery peak corroborate it.

    Important caveat, included in the output: on days with a run, the stored
    readiness score was captured AFTER the run (the auto-sync refreshes the row
    every 2 hours, so the value kept is the last one before UTC midnight). It
    therefore understates how ready the athlete was that morning, and a score of
    1-5 on a run day is an artifact, not a verdict.

    Args:
        days: how many days back to return (1-90).
    """
    try:
        days = max(1, min(90, days))
        from models import DailyWellness
        async with async_session() as session:
            rows = (await session.execute(
                select(DailyWellness).order_by(DailyWellness.date.desc()).limit(days)
            )).scalars().all()
            if not rows:
                return "No wellness data recorded."
            lines = ["date | readiness | sleep | battery | HRV | restingHR"]
            for w in reversed(rows):
                lines.append(
                    f"{w.date} | {w.readiness_score} {w.readiness_level or ''} | "
                    f"{w.sleep_hours}h (score {w.sleep_score}) | "
                    f"{w.body_battery_peak} | {w.hrv_last_night} {w.hrv_status or ''} | "
                    f"{w.resting_hr}"
                )
            lines.append("")
            lines.append(
                "Caveat: on run days the readiness score was captured after the run, "
                "so a very low value there reflects the session just completed rather "
                "than the athlete's state that morning."
            )
            return mv.cap_text("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_recovery failed")
        return f"Could not read recovery data: {exc}"


@mcp.tool()
async def get_records() -> str:
    """Personal records by distance, plus the sprint baseline.

    PRs for 1k/2k/3k/5k/10k with the date and pace, the best single 1 km split,
    and the sprint profile (best 100 m and 200 m, top speed, fade percentage and
    the resulting diagnosis).
    """
    try:
        import main as _main
        async with async_session() as session:
            prs = await _main.personal_records(session)
            base = await _main.get_sprint_baseline(session)
            return mv.cap_text(
                "Personal records:\n" + _json_block(prs)
                + "\n\nSprint baseline:\n" + _json_block(base)
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_records failed")
        return f"Could not read records: {exc}"


@mcp.tool()
async def get_training_context() -> str:
    """Where the athlete is in their training right now.

    The current phase, the active plan and its week number, today's planned
    workout with the readiness-based recommendation, and progress toward the
    sub-6:00/km gate — reported both as the raw best pace and as the
    weather-normalized equivalent, because dew point has been costing 20+ s/km.
    """
    try:
        import main as _main
        async with async_session() as session:
            phases = await _main.get_phases(session)
            plan = await _main.get_active_plan(session)
            try:
                guidance = await _main.today_guidance(session=session)
            except Exception:  # noqa: BLE001 — guidance needs the watch, may be absent
                guidance = {"note": "today's guidance unavailable"}

            acts = (await session.execute(
                select(Activity)
                .where(Activity.moving_time.isnot(None), Activity.distance > 1000)
                .order_by(Activity.start_date.desc()).limit(15)
            )).scalars().all()
            best_raw = min(
                (a.moving_time / (a.distance / 1000.0) for a in acts),
                default=None,
            )
            best_norm = min(
                (a.normalized_pace_sec for a in acts if a.normalized_pace_sec),
                default=None,
            )
            gate = (
                f"Gate (sub-6:00/km): best of last 15 runs is "
                f"{_fmt_pace(best_raw)} raw, {_fmt_pace(best_norm)} weather-normalized."
            )
            return mv.cap_text(
                "Phases:\n" + _json_block(phases)
                + "\n\nActive plan:\n" + _json_block(plan)
                + "\n\nToday:\n" + _json_block(guidance)
                + "\n\n" + gate
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("get_training_context failed")
        return f"Could not read training context: {exc}"
```

And add the JSON helper near `_fmt_pace`:

```python
import json


def _json_block(value) -> str:
    """Compact JSON, heavy fields removed."""
    return json.dumps(mv.strip_heavy(value), default=str, indent=1)[:6000]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: `11 passed`

If `personal_records`, `get_sprint_baseline`, `get_phases`, `get_active_plan` or
`today_guidance` have different names or signatures in `main.py`, grep for the actual
endpoint function names (`grep -n 'async def' main.py`) and use those — the endpoints are
plain async functions taking a session, as `tests/test_plan_endpoints.py` demonstrates.

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): compare, recovery, records and training-context tools"
```

---

## Task 8: `sync_garmin`

**Files:**
- Modify: `backend/mcp_server.py`
- Test: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_sync_garmin_reports_the_imported_count(monkeypatch):
    main = await _seed(monkeypatch, "k")
    import mcp_server

    async def fake_sync(session):
        return {"imported": 2, "error": None}
    monkeypatch.setattr(mcp_server, "_garmin_sync", fake_sync)

    out = await mcp_server.sync_garmin()

    assert "2" in out


@pytest.mark.asyncio
async def test_sync_garmin_times_out_without_hanging(monkeypatch):
    main = await _seed(monkeypatch, "l")
    import asyncio
    import mcp_server

    async def slow_sync(session):
        await asyncio.sleep(5)
    monkeypatch.setattr(mcp_server, "_garmin_sync", slow_sync)
    monkeypatch.setattr(mcp_server, "SYNC_TIMEOUT_SEC", 0.1)

    out = await mcp_server.sync_garmin()

    assert "still running" in out.lower()


@pytest.mark.asyncio
async def test_sync_garmin_surfaces_an_error_readably(monkeypatch):
    main = await _seed(monkeypatch, "m")
    import mcp_server

    async def broken_sync(session):
        raise RuntimeError("no garmin token")
    monkeypatch.setattr(mcp_server, "_garmin_sync", broken_sync)

    out = await mcp_server.sync_garmin()

    assert "no garmin token" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute 'sync_garmin'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_server.py`:

```python
import asyncio

# Garmin's import pages through the activity list, so it can outlast a tool
# call. Bounded, and the work keeps running server-side past the timeout.
SYNC_TIMEOUT_SEC = 60.0


async def _garmin_sync(session):
    """Indirection so tests can substitute the import without touching Garmin."""
    import main as _main
    return await _main.import_garmin_sync(session)


@mcp.tool()
async def sync_garmin() -> str:
    """Pull any new runs from Garmin into RunFlow.

    The only action in this connector — everything else is read-only. Safe to
    call repeatedly: the import is deduplicated by Garmin activity id and stops
    once it reaches runs already stored.

    Returns the number of runs imported. If the import outlasts the timeout it
    keeps running on the server and the result says so — call list_recent_runs a
    minute later to see what landed.
    """
    try:
        async with async_session() as session:
            try:
                res = await asyncio.wait_for(
                    _garmin_sync(session), timeout=SYNC_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                return (
                    "Sync still running on the server — it outlasted the "
                    f"{int(SYNC_TIMEOUT_SEC)}s tool timeout. Check "
                    "list_recent_runs shortly to see what was imported."
                )
        res = res or {}
        imported = res.get("imported", 0)
        err = res.get("error")
        msg = f"Imported {imported} new run(s) from Garmin."
        return msg + (f" Warning: {err}" if err else "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("sync_garmin failed")
        return f"Garmin sync failed: {exc}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): sync_garmin with a bounded timeout"
```

---

## Task 9: `call_api` escape hatch

**Files:**
- Modify: `backend/mcp_server.py`
- Test: `backend/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_call_api_rejects_a_post_only_path(monkeypatch):
    main = await _seed(monkeypatch, "n")
    import mcp_server

    out = await mcp_server.call_api(path="/api/import/garmin/sync")

    assert "get" in out.lower()
    assert "not" in out.lower()


@pytest.mark.asyncio
async def test_call_api_rejects_an_unknown_path_and_suggests_matches(monkeypatch):
    main = await _seed(monkeypatch, "o")
    import mcp_server

    out = await mcp_server.call_api(path="/api/stats/nonsense")

    assert "no get route" in out.lower() or "unknown" in out.lower()
    assert "/api/stats" in out


@pytest.mark.asyncio
async def test_call_api_returns_data_for_a_valid_get(monkeypatch):
    main = await _seed(monkeypatch, "p")
    import mcp_server

    out = await mcp_server.call_api(path="/api/stats/personal-records")

    assert isinstance(out, str) and out
    assert "error" not in out.lower()[:40]


@pytest.mark.asyncio
async def test_call_api_strips_heavy_fields_by_default(monkeypatch):
    main = await _seed(monkeypatch, "q")
    import mcp_server

    out = await mcp_server.call_api(path="/api/activities")

    assert "map_summary_polyline" not in out


@pytest.mark.asyncio
async def test_call_api_accepts_a_path_without_a_leading_slash(monkeypatch):
    main = await _seed(monkeypatch, "r")
    import mcp_server

    out = await mcp_server.call_api(path="api/stats/personal-records")

    assert "no get route" not in out.lower()


def test_get_route_index_lists_only_get_paths():
    import mcp_server

    paths = mcp_server._get_route_index()

    assert "/api/activities" in paths
    assert "/api/import/garmin/sync" not in paths
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute 'call_api'`

- [ ] **Step 3: Write the minimal implementation**

Append to `backend/mcp_server.py`:

```python
def _get_route_index() -> list[str]:
    """Every registered GET path, read from the app's own route table.

    Generated rather than hand-listed so it can never drift from the real API.
    """
    import main as _main
    paths = set()
    for r in _main.app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if "GET" in methods and path.startswith("/api"):
            paths.add(path)
    return sorted(paths)


@mcp.tool()
async def call_api(
    path: str,
    params: Optional[dict] = None,
    include_heavy: bool = False,
) -> str:
    """Last resort: call a RunFlow REST endpoint directly.

    PREFER THE SPECIFIC TOOLS. list_recent_runs, get_run_detail, compare_runs,
    get_recovery, get_records and get_training_context return analysed summaries
    — splits, drift, zone shares, pauses, weather-normalized pace — that this
    tool does not compute. Use call_api only when none of them covers the
    question, for example a route-level or monthly-stats breakdown.

    GET routes only, so nothing here can change data. Encoded GPS polylines and
    raw sample streams are removed unless include_heavy is true, and streams are
    then thinned to at most 200 points (the true sample count is reported).
    Output is capped at 25,000 characters.

    Args:
        path: the route path, e.g. "/api/stats/monthly". Leading slash optional.
        params: optional query parameters.
        include_heavy: include polylines and downsampled streams.
    """
    try:
        import httpx
        import main as _main

        if not path.startswith("/"):
            path = "/" + path
        index = _get_route_index()
        if path not in index:
            near = [p for p in index if p.split("/")[:3] == path.split("/")[:3]]
            hint = ", ".join(near[:8]) if near else ", ".join(index[:12])
            return (
                f"No GET route '{path}'. Closest available: {hint}\n\n"
                "Note: POST routes are not callable through this tool."
            )

        transport = httpx.ASGITransport(app=_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://runflow.internal"
        ) as client:
            resp = await client.get(path, params=params or {}, timeout=30.0)
        if resp.status_code != 200:
            return f"{path} returned HTTP {resp.status_code}: {resp.text[:400]}"

        data = resp.json()
        if include_heavy:
            data = _thin_streams(data)
        else:
            data = mv.strip_heavy(data)
        return mv.cap_text(json.dumps(data, default=str, indent=1))
    except Exception as exc:  # noqa: BLE001
        logger.exception("call_api failed for %s", path)
        return f"Could not call {path}: {exc}"


def _thin_streams(value):
    """Downsample any stream-shaped list rather than dropping it."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "data" and isinstance(v, list) and len(v) > mv.MAX_STREAM_POINTS:
                out[k] = mv.downsample(v)
            else:
                out[k] = _thin_streams(v)
        return out
    if isinstance(value, list):
        return [_thin_streams(v) for v in value]
    return value
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && ./venv/bin/python -m pytest tests/test_mcp_tools.py -q`
Expected: `20 passed`

- [ ] **Step 5: Verify the whole suite**

Run: `cd backend && ./venv/bin/python -m pytest -q`
Expected: `1 failed, N passed` (the pre-existing `test_run_chat` failure only)

- [ ] **Step 6: Commit**

```bash
git add backend/mcp_server.py backend/tests/test_mcp_tools.py
git commit -m "feat(mcp): GET-only call_api escape hatch with pruning and caps"
```

---

## Task 10: Deploy

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `DEPLOYMENT.md`, `IDEAS.md`

- [ ] **Step 1: Add proxy headers to the uvicorn command**

nginx terminates TLS, so without this the `/mcp` → `/mcp/` redirect is emitted as `http://`
and MCP clients refuse to follow it. In `backend/Dockerfile`, change the `CMD` to:

```dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
```

- [ ] **Step 2: Generate the secret and put it on the VM**

```bash
openssl rand -hex 20
```

Then, with that value:

```bash
gcloud compute ssh socialflow --project=polar-pillar-450607-b7 --zone=us-east1-d \
  --tunnel-through-iap --command="echo 'MCP_SECRET=<value>' | sudo tee -a /opt/runflow/.env"
```

- [ ] **Step 3: Commit, push, and deploy**

```bash
git add backend/Dockerfile DEPLOYMENT.md IDEAS.md
git commit -m "chore(mcp): deploy the MCP connector"
git push origin main
```

```bash
gcloud compute ssh socialflow --project=polar-pillar-450607-b7 --zone=us-east1-d \
  --tunnel-through-iap --command="cd /opt/runflow && sudo git pull && \
  cd backend && sudo docker build -q -t runflow-backend . && \
  sudo docker rm -f runflow-backend && \
  sudo docker run -d --name runflow-backend --restart unless-stopped -p 8020:8000 \
    -v /opt/runflow/.env:/app/.env -v /opt/runflow/data:/data \
    -e ENV_PATH=/app/.env -e DB_PATH=/data/training.db runflow-backend && \
  sleep 8 && sudo docker ps --filter name=runflow-backend --format '{{.Status}}'"
```

Expected: `Up 8 seconds`

- [ ] **Step 4: Verify the endpoint from outside**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://runflow-api.skdev.one/mcp/wrong/mcp
curl -s -X POST https://runflow-api.skdev.one/mcp/<secret>/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 600
```

Expected: the wrong path returns `404`. The correct path returns a JSON-RPC response
listing the eight tools — **not** `421` (which would mean `allowed_hosts` is wrong) and
**not** `404`.

- [ ] **Step 5: Document it**

Add to `DEPLOYMENT.md` a "MCP connector" section with: the connector URL shape, that
`MCP_SECRET` lives in `/opt/runflow/.env`, how to rotate it (change, restart, re-paste in
Claude), and this commented-out nginx block:

```nginx
# Restrict the MCP endpoint to Anthropic's cloud egress range. Off by default:
# the range is third-party-documented and may change, and it also blocks local
# testing. Uncomment to harden.
# location /mcp/ {
#     allow 160.79.104.0/21;
#     deny all;
#     proxy_pass http://127.0.0.1:8020;
#     proxy_set_header Host $host;
#     proxy_set_header X-Forwarded-Proto $scheme;
# }
```

Tick the MCP connector line in `IDEAS.md` under Completed.

- [ ] **Step 6: Add it in the Claude app**

Settings → Connectors → Add custom connector → paste
`https://runflow-api.skdev.one/mcp/<secret>/mcp`. Leave the OAuth fields empty.
Then verify in the Claude app by asking "what were my last three runs?" and confirming
`list_recent_runs` is called.

- [ ] **Step 7: Commit the docs**

```bash
git add DEPLOYMENT.md IDEAS.md
git commit -m "docs(mcp): connector URL, secret rotation, nginx allowlist"
git push origin main
```

---

## Self-review against the spec

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `mcp_views.py` pure, unit-tested | 2, 3, 4 |
| `mcp_server.py` tools + ASGI app | 5–9 |
| `MCP_SECRET` in config, fails closed | 1, 5 |
| Secret-in-path mount | 5 |
| `transport_security` allowed_hosts (421 trap) | 5 |
| Lifespan `session_manager.run()` (task-group trap) | 5 |
| uvicorn `--proxy-headers` | 10 |
| `list_recent_runs` incl. `fragmented` | 6 |
| `get_run_detail`: splits/drift/time-to-zone/zones/pauses/dynamics/weather | 6 |
| `compare_runs`, ≤5 cap | 7 |
| `get_recovery` + post-run readiness caveat | 7 |
| `get_records`, `get_training_context` + gate | 7 |
| `sync_garmin` bounded at 60 s | 8 |
| `call_api` GET-only, generated index, `/api` prefix, optional slash | 9 |
| Heavy stripping, 200-point downsample, 25k cap | 4, 9 |
| Per-tool error handling, readable empty results | 6–9 |
| Pure view tests + temp-DB tool tests | 2–9 |
| Deploy, verification, nginx block, docs | 10 |

No gaps.

**Placeholders:** none — every code step carries complete code; `<secret>` and `<value>`
are runtime values the engineer generates in Task 10 Step 2.

**Type consistency:** `mv.find_pauses`, `mv.moving_time_axis`, `mv.split_table`,
`mv.cardiac_drift`, `mv.seconds_to_cross`, `mv.zone_shares`, `mv.strip_heavy`,
`mv.downsample`, `mv.cap_text`, `mv.MAX_STREAM_POINTS`, `mv.MAX_RESPONSE_CHARS` are each
defined in Tasks 2–4 and used under those exact names in Tasks 6–9. `_fmt_pace`,
`_fmt_dur`, `_json_block`, `_streams_for`, `_garmin_sync`, `SYNC_TIMEOUT_SEC`,
`_get_route_index`, `_thin_streams` are defined before first use. `mcp`, `asgi_app` and
`MOUNT_PATH` are defined in Task 5 and referenced from `main.py` in that same task.

One known risk carried from the spec: Task 7 calls `main.py` endpoint functions
(`personal_records`, `get_sprint_baseline`, `get_phases`, `get_active_plan`,
`today_guidance`) by name. Task 7 Step 4 says to grep for the real names if they differ.
