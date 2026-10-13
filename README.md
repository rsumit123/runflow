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
none of them knew that my "easy" runs were being run at 90% of max heart rate,
which is the reason I kept getting injured.

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
- Plan generation for 5 k through half marathon, plus a separate sprint engine
- Workout push to Garmin — the plan appears on the watch as a structured workout
- Plan adherence: planned versus completed, with a grader that flags easy runs
  run hard
- Plan calibration — re-anchors paces as fitness changes, instead of holding a
  stale prescription
- Readiness from Garmin's recovery metrics, and heat-adjusted pace targets

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
| `strava_client_id`, `strava_client_secret` | Strava import and webhook |
| `GARMIN_TOKENSTORE` | Path to the saved Garmin OAuth token store — see [GARMIN.md](GARMIN.md) |
| `OPENROUTER_API_KEY` | Per-run chat and narrative insights |
| `MCP_SECRET` | Mounts the MCP connector. Unset means it is not mounted at all. |

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

Built for one runner, which shapes the design: SQLite rather than Postgres,
no multi-user model, and defaults tuned to my own physiology. The coaching logic
is general; the thresholds are not.

## License

MIT — see [LICENSE](LICENSE).
