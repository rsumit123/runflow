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

**Tools exposed.** Reads: `list_recent_runs`, `get_run_detail`, `compare_runs`,
`get_recovery`, `get_records`, `get_training_context`, `get_aerobic_trend`,
`get_weekly_volume`, and `call_api` (GET routes only). Writes: `sync_garmin`
(triggers the Garmin import) and `set_run_note` (the athlete's own context on a
run, max 2000 chars — the only field in the database this connector can change).

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
