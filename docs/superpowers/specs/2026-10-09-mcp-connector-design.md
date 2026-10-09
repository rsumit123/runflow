# RunFlow MCP Connector — Design

Expose RunFlow to the Claude app as a remote MCP server, so coaching conversations
("how was today's run?", "am I still redlining?") work on a phone without Claude Code.

Scope: **read + trigger-sync**. No writes to training data — no plan create/abandon,
no goal setting, no guidance accept. One deliberate action (`sync_garmin`); everything
else is read-only.

Build order: **M1** (views + tools + mount + auth, deployed), then **M2** (escape hatch
hardening) if the generated endpoint index proves too large for a tool description.

## Grounding facts

- Backend: FastAPI, 62 `@app.get`/`@app.post` routes in `backend/main.py`, live at
  `https://runflow-api.skdev.one` (nginx + certbot → container on port 8020).
- **The REST API has no authentication.** Any POST, including the backfills, is callable
  by anyone. This design does not change that (deliberately — the Vercel frontend depends
  on it) but it must not *extend* that exposure, hence the secret-in-path mount below.
- `main.py:92` already defines an `asynccontextmanager` lifespan doing `init_db()` plus
  spawning `_auto_sync_loop()`. `app = FastAPI(title="RunFlow", lifespan=lifespan)` at
  `main.py:100`.
- Payloads are heavy: every activity in `/api/activities` carries
  `map_summary_polyline` (hundreds of chars of encoded GPS); activity detail carries nine
  streams of ~1,240 samples each. A naive proxy would spend the context window on these.
- Claude app custom connectors accept **authless or OAuth only** — there is no field for a
  pasted bearer token or custom header. Transport must be **Streamable HTTP**. Requests
  originate from Anthropic's cloud (egress `160.79.104.0/21`), so the endpoint must be
  publicly reachable over HTTPS. Requires a Pro/Max/Team/Enterprise plan.

## SDK mechanics (verified against py.sdk.modelcontextprotocol.io)

- PyPI package `mcp`; `from mcp.server import MCPServer`.
- `@mcp.tool()` on a type-hinted function with a docstring; hints become the schema.
- `mcp.streamable_http_app(transport_security=...)` returns a Starlette ASGI app.
- Mounting at prefix `P` puts the endpoint at `P/mcp` (the default `/mcp` path is joined
  to the mount prefix).

Three failure modes to design around, all documented:

1. **A mounted sub-app's lifespan never runs.** The host lifespan must enter
   `async with mcp.session_manager.run():` or the first request raises
   `RuntimeError: Task group is not initialized`.
2. **`mcp.session_manager` only exists after `streamable_http_app()` is called.** So the
   ASGI app is built at module import, and the lifespan only touches the session manager.
3. **Non-localhost `Host` headers are rejected with `421 Misdirected Request`** unless
   `TransportSecuritySettings(allowed_hosts=[...])` is passed. A non-localhost `host=`
   argument does *not* allowlist the hostname.

Plus: nginx terminates TLS, so uvicorn needs `--proxy-headers --forwarded-allow-ips=*`,
otherwise the `/mcp` → `/mcp/` redirect is emitted as `http://` and clients refuse it.

## Architecture

Two new modules, mounted into the existing app. No second service, no second deploy.

```
backend/mcp_views.py    pure functions: DB rows -> compact summaries. No I/O. Unit-tested.
backend/mcp_server.py   MCPServer instance, tool definitions, the ASGI app.
backend/main.py         +4 lines: import, lifespan wrap, conditional mount.
```

Tools open their own `async_session()` and call the same helpers the REST endpoints call,
so query logic is not duplicated. `mcp_views.py` holds presentation only — it is where the
splits/drift/zone analysis lives, and it is the part most likely to break silently, so it
is pure and tested in isolation (same pattern as `fitness_model.py`).

### Auth

Mount path carries the secret:

```python
MCP_SECRET = os.getenv("MCP_SECRET")          # in config.py, read from the existing .env
if MCP_SECRET:
    app.mount(f"/mcp/{MCP_SECRET}", mcp_asgi)
```

Connector URL: `https://runflow-api.skdev.one/mcp/<40-char-token>/mcp`

**Fails closed**: with `MCP_SECRET` unset the mount never happens, so a missing secret
yields 404 rather than an open endpoint. The token is the credential — treat the URL as
secret. Rotation is: change the env var, restart, re-paste in Claude.

Defence in depth, shipped commented-out in `DEPLOYMENT.md`: an nginx `allow`/`deny` block
for `160.79.104.0/21`. Left off by default because that range is third-party-documented,
could change, and would also block local testing.

No OAuth. If this is ever shared with another person, OAuth with dynamic client
registration becomes necessary — explicitly out of scope here.

## Tool surface

Eight tools. Signatures are the MCP schema (type hints), so these are the contract.

### `list_recent_runs(limit: int = 10, since: str | None = None) -> str`
Most recent runs, newest first. `since` is an ISO date. Per run: id, date, distance,
pace, avg/max HR, Z5 %, aerobic TE + label, heat penalty, and `fragmented: bool` — true
when the time stream contains at least one gap > 3 s with no distance gained, i.e. the run
was stopped and restarted. No polyline, no streams.

### `get_run_detail(run_id: int) -> str`
The full single-run analysis:
- 500 m splits: pace + mean/peak HR per split, computed on **moving time** (pauses excluded)
- cardiac drift (first-half vs second-half mean HR), with the caveat that drift is
  inflated by a genuinely easy start and so must be read alongside the zone shares
- seconds to first cross Z4 and Z5
- zone breakdown (%) on current recomputed boundaries
- pauses: location in km, duration, HR entering and leaving
- running dynamics (cadence, stride length, GCT, vertical oscillation)
- weather: temp, dew point, combined index, heat penalty, cool-day equivalent pace

### `compare_runs(run_ids: list[int]) -> str`
2–5 runs, headline metrics side by side: distance, pace, avg HR, Z5 %, drift, TE, dew
point, normalized pace. Rejects >5 with a readable message.

### `get_recovery(days: int = 14) -> str`
Per day: readiness score + level, sleep hours/score, body battery peak, HRV last-night +
status, resting HR. Includes a standing note that **run-day readiness is captured after
the run** (the auto-sync refreshes the row every 2 h, so the stored value is the last
sync before UTC midnight) and therefore understates morning readiness on days with a run.

### `get_records() -> str`
PRs by distance (1k/2k/3k/5k/10k) with date and pace, best 1 km split, plus the sprint
baseline (best 100 m/200 m, top speed, fade %, diagnosis).

### `get_training_context() -> str`
Current phase (runs, weeks, volume), active plan (goal type, target, week number, status),
today's planned workout and the guidance recommendation, and progress toward the
sub-6:00/km gate — both raw and weather-normalized best pace.

### `sync_garmin() -> str`
Triggers `import_garmin_sync`. Bounded at 60 s via `asyncio.wait_for`; on timeout returns
"sync still running, N imported so far" rather than hanging the tool call. Returns the
imported count and any error string.

### `call_api(path: str, params: dict | None = None, include_heavy: bool = False) -> str`
The escape hatch, for detail the curated tools don't cover.

Constraints, all enforced server-side:
- **GET only.** `path` is the full route path including the `/api` prefix (e.g.
  `/api/stats/monthly`); a leading slash is optional and added if missing. It is resolved
  against `app.routes`; anything not a registered GET route returns an error naming the
  closest matches. Nothing destructive is reachable.
- **Heavy fields stripped** unless `include_heavy=True`: `map_summary_polyline` and
  `streams` are removed from the response.
- **Streams downsampled** to ≤200 points when `include_heavy=True`, with the original
  sample count stated so a downsample never reads as the full series.
- **25,000 character cap**, truncated with an explicit marker. A silent truncation that
  looks like complete data is the failure mode being prevented.

Its description states it is a last resort and that the specific tools return better
analysis. The list of available GET paths is generated from `app.routes` at import time,
so it cannot drift from the real API.

**Known risk, accepted by the user:** a generic tool tends to attract calls that a
specific tool would answer better. Mitigated by description wording and by the curated
tools returning strictly more useful output for the questions they cover. If the generated
path index makes the description unwieldy in practice, it moves to a separate
`list_api_endpoints()` tool (M2) — the constraint logic is unaffected.

## Error handling

Every tool wraps its body and returns a readable string on failure. A raising MCP tool
gives Claude nothing actionable; a sentence naming what failed does. DB and Garmin errors
are caught separately from programming errors so a bug surfaces as a bug rather than as
"no data".

Empty results return an explicit "no runs in that range" rather than an empty table.

## Testing

Test-first throughout, matching existing conventions.

`tests/test_mcp_views.py` — pure unit tests over synthetic activities:
- 500 m splits with and without pauses (a pause must not inflate a split's pace)
- drift computation, including the easy-start case where drift is high but zone shares improve
- zone shares against known boundaries
- pause detection: gap > 3 s with no distance gained
- stream downsampling preserves first/last and reports the true original count
- heavy-field stripping removes polyline and streams

`tests/test_mcp_tools.py` — tools against a temp SQLite DB via the `importlib.reload`
pattern from `tests/test_plan_endpoints.py`:
- each tool returns non-empty output on seeded data and a readable message on empty data
- `call_api` rejects a POST path, an unknown path, and caps an oversized response
- `call_api` strips heavy fields by default and downsamples when asked
- `compare_runs` rejects more than five ids

Not tested: the MCP transport/mount itself (SDK-owned) and live Garmin calls.

## Deployment

1. `mcp` added to `backend/requirements.txt`
2. `MCP_SECRET` generated (`openssl rand -hex 20`) and appended to `/opt/runflow/.env`
3. Dockerfile `CMD` gains `--proxy-headers --forwarded-allow-ips=*`
4. git pull → docker build → docker rm/run on the `socialflow` VM (per `DEPLOYMENT.md`)
5. Verify: `curl -s -X POST https://runflow-api.skdev.one/mcp/<secret>/mcp` returns an MCP
   error (not 404 and not 421), and `/mcp/wrong/mcp` returns 404
6. Claude app → Settings → Connectors → add custom connector → paste the URL
7. `DEPLOYMENT.md` and `IDEAS.md` updated

## Out of scope

- OAuth / sharing with other people
- Any write to plans, goals, guidance, or route labels
- MCP resources and prompts (tools only)
- Locking down the existing REST API
- Fixing the post-run readiness capture bug (noted in `get_recovery`, tracked separately)
