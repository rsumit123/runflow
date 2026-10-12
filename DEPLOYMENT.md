# RunFlow Deployment Guide

## Architecture

- **Frontend**: React app deployed on **Vercel** at `https://runflow.skdev.one`
- **Backend**: FastAPI app in Docker on **GCP VM** at `https://runflow-api.skdev.one`
- **Database**: SQLite file at `/opt/runflow/data/training.db` on the GCP VM
- **SSL**: Let's Encrypt via Certbot (backend), Vercel automatic (frontend)

## Domains & DNS

| Domain | Service | DNS |
|--------|---------|-----|
| `runflow.skdev.one` | Vercel (frontend) | CNAME → `cname.vercel-dns.com` (Namecheap) |
| `runflow-api.skdev.one` | GCP VM (backend) | A record → `34.23.158.39` (Namecheap) |

## GitHub Repository

`https://github.com/rsumit123/runflow.git` — branch: `main`

---

## Frontend Deployment (Vercel)

### Project: `runflow` on Vercel

**Build config** (in `frontend/vercel.json`):
- Build command: `REACT_APP_API_URL=https://runflow-api.skdev.one/api npm run build`
- Output directory: `build`
- Rewrites: SPA fallback to `index.html`

### Deploy

```bash
cd frontend
npx vercel --prod --yes
```

### Environment

The API URL is baked in at build time via `REACT_APP_API_URL`. For local dev, it defaults to `http://localhost:8000/api`.

---

## Backend Deployment (GCP VM)

### Server Details

- **GCP Project**: `polar-pillar-450607-b7`
- **VM Name**: `socialflow`
- **Zone**: `us-east1-d`
- **SSH**: `gcloud compute ssh socialflow --project=polar-pillar-450607-b7 --zone=us-east1-d --tunnel-through-iap`
- **SSH Alias**: `ssh-social` (defined in shell config)
- **Port**: `8020` (mapped to container port `8000`)

### Directory Structure on VM

```
/opt/runflow/
├── .env                  # Strava API credentials (not in git)
├── data/
│   └── training.db       # SQLite database (persistent volume)
├── backend/
│   ├── Dockerfile
│   ├── main.py
│   └── ...
└── frontend/             # Not used on VM (deployed via Vercel)
```

### Docker Setup

Container name: `runflow-backend`

```bash
# Build
cd /opt/runflow/backend
sudo docker build -t runflow-backend .

# Run
sudo docker run -d \
  --name runflow-backend \
  --restart unless-stopped \
  -p 8020:8000 \
  -v /opt/runflow/.env:/app/.env \
  -v /opt/runflow/data:/data \
  -e ENV_PATH=/app/.env \
  -e DB_PATH=/data/training.db \
  runflow-backend
```

### Nginx Config

File: `/etc/nginx/sites-enabled/runflow-api.skdev.one`

Proxies `runflow-api.skdev.one` → `localhost:8020` with SSL managed by Certbot.

### SSL Certificate

```bash
sudo certbot --nginx -d runflow-api.skdev.one --non-interactive --agree-tos --redirect
```

Auto-renews via Certbot's systemd timer.

---

## Updating the App

### Backend Update

```bash
# SSH into VM
gcloud compute ssh socialflow --project=polar-pillar-450607-b7 --zone=us-east1-d --tunnel-through-iap

# Pull latest code
cd /opt/runflow && git pull

# Rebuild and restart
cd backend
sudo docker build -t runflow-backend .
sudo docker rm -f runflow-backend
sudo docker run -d \
  --name runflow-backend \
  --restart unless-stopped \
  -p 8020:8000 \
  -v /opt/runflow/.env:/app/.env \
  -v /opt/runflow/data:/data \
  -e ENV_PATH=/app/.env \
  -e DB_PATH=/data/training.db \
  runflow-backend
```

### Frontend Update

```bash
cd frontend
npx vercel --prod --yes
```

---

## Other Services on the Same VM

The GCP VM hosts multiple projects. Do not conflict with these ports:

| Port | Service |
|------|---------|
| 8000 | willow-leather-api |
| 8001 | chillbill-api |
| 8005 | charade-backend |
| 8010 | defense-game-backend |
| 8015 | rasoi-backend |
| 8020 | **runflow-backend** |
| 8080 | socialflow (nginx → Django) |

---

## Environment Variables (.env)

Required in `/opt/runflow/.env`:

```
strava_client_id=<your_client_id>
strava_client_secret=<your_client_secret>
strava_access_token=<auto_refreshed>
strava_refresh_token=<auto_refreshed>
```

Tokens are auto-refreshed by the app when they expire.

## Strava OAuth Callback

The OAuth callback URL configured in the Strava API app must match:
- **Local dev**: `http://localhost:8000/api/auth/callback`
- **Production**: `https://runflow-api.skdev.one/api/auth/callback`

Update the Strava API app settings at `https://www.strava.com/settings/api` if needed.

## Troubleshooting

```bash
# Check container logs
sudo docker logs runflow-backend --tail 50

# Check if container is running
sudo docker ps | grep runflow

# Restart container
sudo docker restart runflow-backend

# Check nginx
sudo nginx -t && sudo systemctl reload nginx

# Renew SSL
sudo certbot renew
```

---

## MCP Connector (Claude app)

RunFlow is exposed to the Claude app as a remote MCP server mounted inside the
same FastAPI container — no second service.

**Connector URL** (Claude app → Settings → Connectors → Add custom connector,
leave the OAuth fields empty):

```
https://runflow-api.skdev.one/mcp/$MCP_SECRET/mcp
```

The secret is in the path because Claude app custom connectors support only
authless or OAuth servers — there is no field for a bearer token. **Treat the
whole URL as a credential.** Requires a Pro/Max/Team/Enterprise plan.

**Where the secret lives:** `MCP_SECRET` in `/opt/runflow/.env` on the
`socialflow` VM. If it is unset the server is not mounted at all, so the
endpoint returns 404 rather than being open.

**Rotating it:**
```bash
NEW=$(openssl rand -hex 20)
gcloud compute ssh socialflow --project=polar-pillar-450607-b7 --zone=us-east1-d \
  --tunnel-through-iap --command="sudo sed -i 's|^MCP_SECRET=.*|MCP_SECRET=$NEW|' /opt/runflow/.env && sudo docker restart runflow-backend"
```
Then re-paste the new URL in the Claude app.

**Tools exposed.** Reads: `list_recent_runs`, `get_run_detail`, `review_run`,
`compare_runs`, `get_recovery`, `get_records`, `get_training_context`,
`get_fitness_model`, `get_aerobic_trend`, `get_weekly_volume`,
`get_training_block`, `get_plan_status`, and `call_api` (GET routes only).
Writes: `sync_garmin` (triggers the Garmin import) and `set_run_note` (the
athlete's own context on a run, max 2000 chars — the only field in the database
this connector can change). Fifteen tools in total.

**There are no plan write tools yet.** Nothing in this connector can create a
plan, move a workout, push one to the watch, or apply a suggestion — those are
plan 2 of 2. The session and suggestion ids the reads emit are stable and are
what those tools will take, but until they exist the ids are for reporting to
the athlete, not for acting on. An earlier draft of `get_plan_status` named
five of them in its docstring; a model reading that would have offered to move
a workout and then failed.

The four fat reads, and why each exists:

- `review_run(run_id, window_sec)` — one run in depth: 500 m splits on moving
  time, HR and pace per window, pauses located with the HR either side of each,
  cardiac drift, terrain, time in zones with the boundaries, the athlete's note,
  and the planned targets beside the actuals when the run matched a session.
  Answers "did I fade?" and "what HR was I at for the first five minutes?",
  which previously meant reading stream JSON out of SQLite over SSH. Both tables
  widen their own interval to stay inside `mcp_reports.MAX_TABLE_ROWS`, so the
  report fits by construction rather than relying on the 25,000-character cap —
  a 4-hour run at 10 s windows once produced 57,252 characters and silently
  dropped cardiac drift, the zones, the note and the whole planned comparison.
  When the adherence lookup fails it says so, rather than reporting "no planned
  session matched", which would state there was nothing planned.
- `get_fitness_model()` — easy pace with its method (`measured`,
  `measured_stale`, `estimate` or `insufficient_data`), and whether the value
  was clamped to the 7:30/km floor with the real measurement beside it; easy HR
  ceiling; max HR; acute and chronic load; and the ACWR. The 1.5 risk threshold
  is stated beside the number **only when the baseline supports it** — below
  `fitness_model.THIN_BASELINE_KM` weekly chronic load the ratio is inflated by
  design and reads as a comeback ramp, which is what `build_warnings` has always
  done. Backed by `GET /api/fitness-model`.
- `get_training_block(start, end, by)` — volume, pace and Zone 5 share per month
  or week over any ISO date range, aggregated server-side so a multi-year
  comparison costs one small response instead of hundreds of rows. Monthly rows
  carry a median pace beside the mean (one long slow run drags the mean) and
  `days_run` beside `runs`. Zone 5 reads as an em dash where no run carried HR
  zones — the pre-2026 history has none, and `0.0%` there would read as a
  measured zero. The weekly rate divides by the weeks a month actually spans, so
  an in-progress month is not understated.
- `get_plan_status()` — the active plan and its target, the sessions in the week
  of the next one due with their ids and whether each is on the watch, adherence
  so far, today's guidance given readiness and heat, and any outstanding
  suggestions. The adherence percentage is withheld below
  `mcp_reports.MIN_ADHERENCE_SAMPLE` past sessions, because 0 of 1 and 0 of 20
  are not the same fact, and a failed guidance lookup is reported as a failure
  rather than as an absence of guidance.

**Verifying after a deploy:**
```bash
# wrong secret must 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://runflow-api.skdev.one/mcp/wrong/mcp
# correct secret must return a session id, not 421 and not 404
curl -si -X POST "https://runflow-api.skdev.one/mcp/$MCP_SECRET/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
  | grep -i mcp-session-id
```
A `421 Misdirected Request` means the hostname is missing from
`_ALLOWED_HOSTS` in `backend/mcp_server.py`. The container log should say
`StreamableHTTP session manager started` on boot.

The uvicorn `CMD` carries `--proxy-headers --forwarded-allow-ips=*`: nginx
terminates TLS, and without them the `/mcp` → `/mcp/` redirect is emitted as
`http://` and MCP clients refuse to follow it.

### Optional hardening — restrict to Anthropic's egress range

Requests reach the connector from Anthropic's cloud, not from your phone. Off by
default because the range is third-party-documented, may change, and would also
block local testing. To enable, add to the nginx server block:

```nginx
# location /mcp/ {
#     allow 160.79.104.0/21;
#     deny all;
#     proxy_pass http://127.0.0.1:8020;
#     proxy_set_header Host $host;
#     proxy_set_header X-Forwarded-Proto $scheme;
# }
```
