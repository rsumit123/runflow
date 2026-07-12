# RunFlow — Garmin Import Design

**Date:** 2026-07-12
**Status:** Approved for planning
**Branch:** `garmin-import`

## Background & Motivation

RunFlow syncs running activities from Strava. As of ~May 2026 the Strava API
application was set to **Inactive** by Strava (API access is now subscriber-only;
the account is on the Free tier), so all Strava data reads return
`403 Forbidden / Application Status: Inactive`. No new runs have synced since
2026-05-18.

Rather than pay for a Strava subscription for a personal analytics tool, the user
has moved to recording runs on **Garmin**. This project adds a Garmin import path
so new runs flow into RunFlow's existing analysis pipeline.

Feasibility was validated on 2026-07-12 with `python-garminconnect`: native
email/password SSO login succeeded (token cached ~1 year), and `get_activities`,
`get_activity_splits`, and `get_activity_details` all returned the required data
(3 recent runs, 7 laps, 1,146 GPS points with lat/lon/time).

## Goals

- Import running activities from Garmin into the existing `Activity` / `Split` /
  `Stream` tables, so **all existing analysis features work unchanged** (route
  maps, route matching, best efforts, laps, intervals, insights, stats).
- Add richer metrics Strava never provided: **heart rate (+ HR zones)** and
  **cadence & running dynamics**.
- Keep it **additive**: Strava code, endpoints, and webhook remain untouched and
  functional (dormant).
- Manual, on-demand sync via a button — no background polling.

## Non-Goals (YAGNI)

- No Garmin webhook (Garmin has none) and no scheduled/daily polling.
- No full Garmin-history backfill (user only started on Garmin ~3 runs ago).
- No changes to the Strava integration.
- No Garmin Coach adaptive-plan data (upcoming workouts, plan progress).
- No training-effect / VO2max / training-load metrics (deferred).
- No weather data (deferred).
- No Stats-page HR/cadence trend charts yet (deferred; per-activity display only).

## Guiding Principle

Symmetry with `strava_client.py`. The Garmin path mirrors the Strava path's shape
so the two coexist cleanly and the import/analysis logic stays uniform. Garmin
data is transformed into the **same stream/split shapes** RunFlow already stores,
then fed through the existing helpers (`encode_polyline`,
`compute_and_store_best_efforts`).

## Architecture

### 1. Authentication — token-store, no password on server

- Library: `python-garminconnect` (native Garmin mobile SSO; does **not** support
  "Sign in with Google", so the account must have a native email/password set).
- **One-time local login** (interactive, handles MFA/rate-limit) produces a
  token-store directory of cached OAuth tokens (~1-year lifetime).
- That directory is copied to the VM's persistent volume at
  `/opt/runflow/data/garmin_tokens/` (mounted into the container like the DB).
- The server loads the token — **the Garmin password is never stored on the VM.**
- Config: `GARMIN_TOKENSTORE` env var, default `/data/garmin_tokens`.
- On token expiry (~1 yr) or invalidation, re-run the local login and re-copy the
  directory. Documented in a new `GARMIN.md` runbook.
- Rate-limit safety: rely on the cached token; never log in per-request. (Repeated
  logins are what triggered the `429`s during validation.)

New module: `backend/garmin_auth.py` — builds/returns an authenticated `Garmin`
client from the token store.

### 2. `GarminClient` wrapper — `backend/garmin_client.py`

`python-garminconnect` is **synchronous**; RunFlow is async. Every Garmin call is
wrapped in `asyncio.to_thread(...)` so it never blocks the event loop. A single
lazily-authenticated shared instance is reused.

Methods (mirroring the Strava client surface):

- `get_recent_running(limit: int) -> list[dict]` — `get_activities(0, limit)`,
  filtered to running types.
- `get_splits(activity_id) -> dict` — `get_activity_splits(activity_id)`.
- `get_streams(activity_id) -> dict` — `get_activity_details(activity_id, ...)`,
  returned for transformation.
- `get_hr_zones(activity_id) -> list` — `get_activity_hr_in_timezones(activity_id)`.

**Running types** treated as runs: `running`, `track_running`, `trail_running`,
`treadmill_running` (matched by suffix `_running` or exact `running`). A helper
`_is_garmin_running(type_key)` centralizes this.

### 3. Data mapping — Garmin → RunFlow schema

**Activity summary** (from `get_activities` item):

| RunFlow field | Garmin source |
|---|---|
| `id` | `activityId` |
| `name` | `activityName` |
| `sport_type` | `activityType.typeKey` |
| `distance` | `distance` (m) |
| `moving_time` | `movingDuration` (s) |
| `elapsed_time` | `duration` (s) |
| `average_speed` / `max_speed` | `averageSpeed` / `maxSpeed` (m/s) |
| `total_elevation_gain` | `elevationGain` |
| `elev_high` / `elev_low` | `maxElevation` / `minElevation` |
| `start_date` | `startTimeGMT` |
| `start_latlng` / `end_latlng` | `startLatitude/Longitude` / `endLatitude/Longitude` |
| `map_summary_polyline` | **derived** via existing `encode_polyline(latlng_stream)` |
| `source` | `'garmin'` |
| `average_heartrate` / `max_heartrate` | `averageHR` / `maxHR` |
| `average_cadence` | `averageRunningCadenceInStepsPerMinute` |

**Splits** (`lapDTOs` from `get_activity_splits`) → `Split` rows:

| Split field | Garmin lap source |
|---|---|
| `split_number` | lap index (1-based) |
| `distance` | `distance` |
| `moving_time` | `movingDuration` |
| `elapsed_time` | `duration` |
| `average_speed` | `averageSpeed` |
| `elevation_difference` | `elevationGain` |
| `average_heartrate` | `averageHR` |
| `average_cadence` | `averageRunCadence` |

**Streams** (from `get_activity_details`):

- `geoPolylineDTO.polyline` (list of points) → `latlng` (`[[lat, lon], ...]`),
  `time` (elapsed seconds from start), `altitude`.
- `activityDetailMetrics` (flat arrays, columns named in `metricDescriptors`) →
  `distance` (sumDistance), `velocity_smooth` (directSpeed), `heartrate`
  (directHeartRate), `cadence` (directRunCadence / directDoubleCadence), and
  running dynamics: `stride_length`, `ground_contact_time`,
  `vertical_oscillation` (device-dependent).

Stored as `Stream` rows (`stream_type` + `data`); the generic table needs no
schema change — only new type strings.

**HR zones** (from `get_activity_hr_in_timezones`) → `Activity.hr_zones` (JSON:
time-in-seconds per zone).

**Running-dynamics summary** → `Activity.running_dynamics` (JSON: avg stride
length, avg ground-contact time, avg vertical oscillation), populated only from
metrics actually present.

### 4. Stream alignment — the one real implementation risk

Garmin's `activityDetailMetrics` is a flat numeric matrix; a separate
`metricDescriptors` block names each column (e.g. `directHeartRate`,
`sumDistance`, `directSpeed`, `directRunCadence`). **Step 1 of implementation** is
to dump one full real `get_activity_details` payload and read the exact descriptor
keys present, then write the mapping against the real data — not against guesses.
This single inspection resolves GPS, distance, speed, HR, cadence, and running
dynamics together.

**Fallback:** if descriptor alignment proves unreliable, download the activity as
FIT (`download_activity(id, FIT)`) and reuse the existing `bulk_import.py`
FIT/GPX parser.

### 5. Graceful degradation

Running dynamics are device/sensor-dependent. The importer creates a stream or
sets a field **only for metrics actually present** in the payload; missing metrics
are skipped silently. The frontend renders a metric panel only when its data
exists — no empty charts, no crashes.

### 6. Import endpoint & reuse

- New: `POST /api/import/garmin/sync` — fetches recent running activities (paging
  in chunks), dedups by activity ID (skip rows that already exist with
  `has_detailed_data`), and **stops paging once a page is entirely already-known**
  — identical incremental logic to the existing Strava `import_sync`. Returns
  `{imported, already_existed, skipped_non_running}`. First sync pulls current
  Garmin history (3 runs today); later syncs fetch ~1 page and stop.
- New helper `_import_garmin_activity(session, summary)` mirroring
  `_import_detail_and_streams`: fetch splits + details (+ HR zones), build splits
  and streams, call `encode_polyline` and `compute_and_store_best_efforts`, set
  `has_detailed_data=True` and `source='garmin'`.
- A small delay is added between pages when multiple pages are fetched.
- Failure/empty fetch degrades gracefully (no unbound-variable 500 — same lesson
  as the `already_existed` bug fixed on this branch).

### 7. Schema migration

Add nullable columns to `Activity`:
`source` (String, default `'strava'`), `average_heartrate`, `max_heartrate`,
`average_cadence` (Float), `hr_zones` (JSON), `running_dynamics` (JSON). Add
`average_cadence` (Float) to `Split`.

Applied on startup via idempotent `ALTER TABLE ... ADD COLUMN` guarded by a
column-existence check (SQLite `PRAGMA table_info`). Existing 621 Strava rows are
untouched and default to `source='strava'`.

### 8. Frontend

- **Import page**: one new **"Sync from Garmin"** button calling
  `POST /api/import/garmin/sync`, showing "Imported N new runs" or a graceful
  error (mirrors the existing Sync button).
- **Activity detail**: HR chart over the run + avg/max + HR-zone bars; a cadence
  chart + avg; a running-dynamics panel shown only when data exists.
- **Splits table**: add HR and cadence columns.

### 9. Dependencies & deploy

- Add `garminconnect` to `backend/requirements.txt`; rebuild the Docker image.
- Ship the token-store directory to `/opt/runflow/data/garmin_tokens/` on the VM.
- No changes to Strava env vars or CORS.

## Error Handling

- **Auth failure** (missing/expired token): endpoint returns a clear message
  ("Garmin token missing or expired — refresh the token file"), not a 500.
- **Rate limit (429)**: surfaced as a friendly "try again shortly"; avoided in
  normal operation by using the cached token and minimal fetching.
- **Missing metrics**: skipped silently (see §5).
- **Per-activity fetch error**: logged, that activity skipped, sync continues.

## Testing / Verification

1. Extend the validation probe to dump one full `get_activity_details` +
   `get_activity_hr_in_timezones` payload; confirm the exact `metricDescriptors`
   keys and write the mapping against them.
2. After building: rebuild the container with the token in place, tap **Sync from
   Garmin**, and confirm a Garmin run renders its **route map, splits, best
   efforts, HR chart + zones, and cadence/dynamics**, and appears in insights and
   stats — driving the real feature end-to-end, not just unit checks.
3. Re-tap Sync; confirm zero duplicates and that it stops after one page.

## Rollout Order

1. Dump real payload → finalize stream mapping.
2. Schema migration (new columns).
3. `garmin_auth.py` + `garmin_client.py`.
4. `_import_garmin_activity` + `/api/import/garmin/sync`.
5. Frontend button + activity-detail HR/cadence/dynamics + splits columns.
6. `garminconnect` dependency, Docker rebuild, token on VM, `GARMIN.md` runbook.
7. End-to-end verification.
