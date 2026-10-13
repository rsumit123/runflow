# RunFlow

A self-hosted running coach. It imports my runs from Garmin and Strava, builds a
fitness model from them, generates a training plan, pushes the workouts to my
watch, and then tells me whether I actually followed it.

[![CI](https://github.com/rsumit123/runflow/actions/workflows/ci.yml/badge.svg)](https://github.com/rsumit123/runflow/actions/workflows/ci.yml)

**Live:** [runflow.skdev.one](https://runflow.skdev.one) · **API docs:** [runflow-api.skdev.one/docs](https://runflow-api.skdev.one/docs)

---

## Why I built it

Every running app I tried would tell me *what* I did — distance, pace, a heart
rate graph — and then stop. None of them would tell me what to do tomorrow, and
none of them noticed that my "easy" runs were being run at close to 90% of max
heart rate — which is not easy running, and not much use as base training.

So RunFlow is built around the part those apps leave out: a model of my current
fitness, a plan derived from it, and an honest comparison between the plan and
what actually happened.

## What it does

**Imports and analysis**

- Garmin and Strava import, including a Strava webhook for automatic sync
- GPS route matching — detects when two runs are the same route and compares them
- Best efforts from 100 m to 2 km, extracted from the GPS stream and mapped
- Lap detection from GPS, and interval analysis for user-defined rep sessions
- Phase detection: finds the training blocks in a multi-year history and labels
  their quality
- Per-run narrative insights that compare a run to recent runs on the same route

**Coaching**

- A fitness model: easy pace, threshold pace, heart rate ceilings, acute and
  chronic load, and acute:chronic workload ratio
- Plan generation for a 5 k goal, with a separate engine for 100 m sprint blocks
- Workout push to Garmin — the plan appears on the watch as a structured workout
- Plan adherence: planned versus completed, with a grader that flags easy runs
  run hard
- Plan calibration — re-anchors paces as fitness changes, instead of holding a
  stale prescription
- Readiness from Garmin's recovery metrics, and heat-adjusted pace targets

**Accounts**

- Sign-up is invite-only. The owner creates an invite code from the server shell
  (`admin.py create-invite`) or Settings, and a new user signs up with it.
- Login is email and password, with a session cookie.
- Each user connects their own Strava and Garmin in Settings. Their tokens are
  stored encrypted, and every query is scoped to the signed-in user.
- Each user gets their own connector URL for the Claude app, which can be
  rotated from Settings.

**Claude connector (MCP)**

RunFlow runs an [MCP](https://modelcontextprotocol.io) server, so the whole
thing is usable as a custom connector from the Claude app on my phone. 24 tools:
run review, the fitness model, training blocks, plan status, and a bounded set
of writes — move a workout, push to the watch, apply a suggestion, create a
plan.

The write surface is deliberately narrow and fails closed. Creating a plan
refuses while another is active unless explicitly told to replace it, and
nothing in the connector can delete a run.

## Architecture

```
┌──────────────────┐         ┌─────────────────────┐
│  React frontend  │ ──────► │   FastAPI backend   │
│     (Vercel)     │  HTTPS  │  (Docker on GCP)    │
└──────────────────┘         └──────────┬──────────┘
                                        │
   ┌──────────────┐                     │
   │  Claude app  │ ──── MCP ───────────┤
   └──────────────┘                     │
                                        ▼
                             ┌─────────────────────┐
                             │   SQLite  (async)   │
                             └─────────────────────┘
                                        ▲
                             ┌──────────┴──────────┐
                             │  Garmin  ·  Strava  │
                             └─────────────────────┘
```

| Layer | Stack |
|---|---|
| Frontend | React, Leaflet, Recharts — deployed on Vercel |
| Backend | FastAPI, Python 3.13, SQLAlchemy async |
| Database | SQLite |
| Integrations | Garmin Connect, Strava API, Open-Meteo |
| Connector | Model Context Protocol server |
| Hosting | Docker on a GCP VM behind nginx, Let's Encrypt via Certbot |

74 HTTP endpoints, 36 backend modules, 434 tests.

## Running it locally

**Backend**

```bash
cd backend
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt greenlet

cp ../.env.example ../.env    # then fill in credentials
uvicorn main:app --reload --port 8000
```

**Frontend**

```bash
cd frontend
npm install
REACT_APP_API_URL=http://localhost:8000/api npm start
```

**Tests**

```bash
cd backend && python -m pytest -q
```

## Configuration

Copy `.env.example` to `.env` in the repository root and fill it in.

| Variable | Purpose |
|---|---|
| `DB_PATH` | SQLite file location |
| `strava_client_id`, `strava_client_secret` | The Strava app used for every user's connection |
| `STRAVA_REDIRECT_URI` | Strava OAuth callback; must be on the app's Authorization Callback Domain |
| `strava_webhook_verify_token` | Token Strava echoes when the webhook is verified |
| `STRAVA_SUBSCRIPTION_ID` | Webhook events from any other subscription are ignored. Empty skips the check. |
| `CREDENTIALS_KEY` | Fernet key that encrypts stored Strava and Garmin tokens |
| `SESSION_STATE_SECRET` | Signs the OAuth `state` of the Strava connect flow |
| `SESSION_COOKIE_SECURE` | `1` (default) for HTTPS; `0` for local http development only |
| `FRONTEND_URL` | Where the browser app lives |
| `PUBLIC_API_URL` | This API's public base URL |
| `GARMIN_TOKENSTORE` | Legacy Garmin token directory, read once by the migration — see [GARMIN.md](GARMIN.md) |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Per-run chat and narrative insights |
| `MCP_SECRET` | Legacy single connector secret, read once by the migration |
| `ENV_PATH` | Alternative path to the `.env` file (optional) |

For local development set `SESSION_COOKIE_SECURE=0`, since the cookie is
otherwise Secure and a browser will not send it over plain http. Create the
first user with `python admin.py` (see [DEPLOYMENT.md](DEPLOYMENT.md)).

## Repository layout

```
backend/          FastAPI app, one module per domain
  main.py           HTTP routes
  mcp_server.py     MCP tool definitions
  fitness_model.py  pace and load model
  plan_generator.py plan construction
  garmin_*.py       import, auth, workout upload
  tests/            40 test files
frontend/src/
  pages/            dashboard, training, activity detail, stats
  components/       maps, charts, tables
DEPLOYMENT.md     deployment runbook
IDEAS.md          feature backlog
```

## Notes

Built for a handful of runners rather than a crowd, which shapes the design:
SQLite rather than Postgres, invite-only accounts in one shared database, and
defaults tuned to my own physiology. The coaching logic is general; the
thresholds are not.

## License

MIT — see [LICENSE](LICENSE).
