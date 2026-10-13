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
| `runflow-api.skdev.one` | GCP VM (backend) | A record → `34.139.217.34` (Namecheap) |

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
- **Port**: `8020` on loopback only (mapped to container port `8000`; nginx is the only way in)

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
  -p 127.0.0.1:8020:8000 \
  -v /opt/runflow/.env:/app/.env \
  -v /opt/runflow/data:/data \
  -e ENV_PATH=/app/.env \
  -e DB_PATH=/data/training.db \
  runflow-backend
```

Publish the port on loopback (`127.0.0.1:8020`), never as plain `8020:8000`. A bare
`-p 8020:8000` binds `0.0.0.0`, and the GCP firewall rule `allow-all-ports`
(0.0.0.0/0) then makes `http://34.139.217.34:8020/api/...` reachable over plain HTTP,
bypassing nginx and TLS. The login rate limiter also trusts nginx's `X-Real-IP`,
which only holds if nginx is the only way in. After every restart, confirm from
outside the VM that this fails:

```bash
curl -m 5 http://34.139.217.34:8020/docs
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
  -p 127.0.0.1:8020:8000 \
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

Required in `/opt/runflow/.env` (placeholders only; generate your own values):

```
strava_client_id=<your_client_id>
strava_client_secret=<your_client_secret>
strava_webhook_verify_token=<random string>
STRAVA_REDIRECT_URI=https://runflow-api.skdev.one/api/strava/callback
STRAVA_SUBSCRIPTION_ID=336241
CREDENTIALS_KEY=<fernet key>
SESSION_STATE_SECRET=<random string>
FRONTEND_URL=https://runflow.skdev.one
PUBLIC_API_URL=https://runflow-api.skdev.one
OPENROUTER_API_KEY=<key>
```

`SESSION_COOKIE_SECURE` defaults to `1`, which is correct in production: the
session cookie is then named `__Host-rf_session` (Secure, host-only, `Path=/`).
Setting it to `0` is for local http development only, and gives a plain
`rf_session` cookie.

Generate the secrets:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # CREDENTIALS_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"                                 # SESSION_STATE_SECRET
```

Back up `CREDENTIALS_KEY` somewhere safe. Without it every stored Strava and
Garmin token is unreadable and each user has to reconnect.

`STRAVA_SUBSCRIPTION_ID` is the id of the webhook subscription (336241). Events
from any other subscription are ignored. Confirm it against Strava with:

```bash
curl -G https://www.strava.com/api/v3/push_subscriptions \
  -d client_id=<your_client_id> -d client_secret=<your_client_secret>
```

Each user connects their own Strava and Garmin in Settings. Their tokens are stored
encrypted in the database and refreshed there; nothing is written to `.env`.

## Strava OAuth Callback

The callback URL used by the app must be:
- **Local dev**: `http://localhost:8000/api/strava/callback`
- **Production**: `https://runflow-api.skdev.one/api/strava/callback`

In the Strava API app settings (`https://www.strava.com/settings/api`), set
**Authorization Callback Domain** to `runflow-api.skdev.one` (domain only, no scheme
or path). Strava refuses the connect flow for any other domain. Strava also caps how
many athletes one app may serve (shown there as Athlete Capacity), so check it before
inviting people.

## Multi-user migration (one-time)

Moves the existing single-athlete database to accounts. All current data is assigned
to the owner, `rsumit123@gmail.com`. Do this once, in this order. It touches the only
copy of the data, so keep the backup it writes.

1. **Strava settings.** Set Authorization Callback Domain to `runflow-api.skdev.one`
   (above). Note the Athlete Capacity, and your Strava athlete id (the number in your
   profile URL).
2. **Back up `.env`**, then add the new variables from the section above. Keep the old
   `strava_access_token`, `strava_refresh_token` and `MCP_SECRET` in it for now:
   the migration reads them once.
3. **Build the new image** (see Backend Update) but do not start it.
4. **Stop the old container:** `sudo docker stop runflow-backend`.
5. **Run the migration** in the new image with `/data` mounted:
   ```bash
   sudo docker run --rm \
     -v /opt/runflow/.env:/app/.env -v /opt/runflow/data:/data \
     -e ENV_PATH=/app/.env -e DB_PATH=/data/training.db \
     runflow-backend python migrate_multiuser.py \
       --backup /data/training.pre-multiuser.db \
       --owner-email rsumit123@gmail.com \
       --strava-athlete-id <athlete_id> --yes
   ```
   It copies the database to `--backup` first and never touches that copy, preflights
   every input, and exits non-zero if any table's row count changes. The preflight
   must report Strava, Garmin and the connector as `found, will move`. It is safe to
   run again: a re-run changes nothing. Check the printed count table against the
   counts before you started.
   - `--yes` is required; without it the migration only preflights and stops.
   - `--no-strava`, `--no-garmin`, `--no-connector` skip a source you do not have.
   - `--fix-owner-email` corrects the owner's email if it was created with a typo.
6. **Keep connector tokens out of nginx logs.** The token is in the URL path. Back up
   `/etc/nginx/sites-available/runflow-api.skdev.one`, then add a `location /mcp/`
   block with `access_log off;` and the same `proxy_*` directives as `location /`.
   Then `sudo nginx -t && sudo systemctl reload nginx`. Check whether the old secret
   was already logged: `sudo grep -c '/mcp/' /var/log/nginx/access.log*`. If it was,
   rotate your connector URL from Settings after the deploy.
7. **Start the new container** with `-p 127.0.0.1:8020:8000` (see Docker Setup).
   Check `curl -s -o /dev/null -w '%{http_code}\n' https://runflow-api.skdev.one/api/activities`
   returns `401`, and that the loopback check above fails from outside.
8. **Set your password** (the owner is created with an unusable password):
   ```bash
   gcloud compute ssh socialflow --zone us-east1-d --project polar-pillar-450607-b7 -- -t \
     sudo docker exec -it runflow-backend python admin.py set-password rsumit123@gmail.com
   ```
9. **Frontend:** merge `multi-user` into `main` and push; Vercel deploys it. Check
   `/` redirects to `/login`, log in, and check the run count matches your baseline.
10. **Cleanup,** once everything works: remove `strava_access_token`,
    `strava_refresh_token` and `MCP_SECRET` from the VM `.env`. Keep the
    pre-migration backup.

**Rollback:** stop the container; restore `/data/training.pre-multiuser.db` over
`/data/training.db`; restore `.env` from its backup; run the previous image; promote
the previous Vercel deployment.

### Admin CLI

Run inside the container, for example
`sudo docker exec -it runflow-backend python admin.py <command>`:

| Command | Does |
|---|---|
| `set-password EMAIL` | Prompts twice for the new password; never pass it as an argument |
| `set-email OLD NEW` | Changes a user's email |
| `create-invite [--uses N] [--expires-days D]` | Prints a sign-up code |
| `list-users` | Lists accounts |
| `delete-user EMAIL --yes` | Removes the user and all their data |

Admins can also create invites from Settings.

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

**Connector URL.** Each user has their own. Open RunFlow, go to Settings, and use
**Create connector URL** (or **Replace connector URL**). The URL is shown once:

```
https://runflow-api.skdev.one/mcp/<token>/mcp
```

Paste it into Claude app → Settings → Connectors → Add custom connector, leaving the
OAuth fields empty. The token is in the path because Claude app custom connectors
support only authless or OAuth servers; there is no field for a bearer token.
**Treat the whole URL as a credential.** Requires a Pro/Max/Team/Enterprise plan.

Every tool call runs as the user who owns the token, so a connector only ever sees
that user's runs. An unknown token returns 404. Replacing the URL in Settings
invalidates the old one at once; re-paste the new URL in the Claude app. The old
single `MCP_SECRET` is read once by the migration and becomes the owner's first
token, so an existing phone connector keeps working.

**Tools exposed.** Reads: `list_recent_runs`, `get_run_detail`, `review_run`,
`compare_runs`, `get_recovery`, `get_records`, `get_training_context`,
`get_fitness_model`, `get_aerobic_trend`, `get_weekly_volume`,
`get_training_block`, `get_plan_status`, and `call_api` (GET routes only).
Writes: `sync_garmin` (triggers the Garmin import), `set_run_note` (the
athlete's own context on a run, max 2000 chars), and nine plan writes:
`set_workout_note`, `move_workout`, `push_workout`, `unpush_workout`,
`sync_plan_to_watch`, `push_adhoc_workout`, `apply_suggestion`,
`apply_calibration` and `create_plan`. Twenty-four tools in total.

**What the writes can and cannot do.** `create_plan` refuses while a plan is
active unless called with `replace_active=True`, because `POST /api/plan`
abandons the active plan and clears the watch unconditionally, with no guard of
its own (`main.py:889`); the refusal names the plan and how many of its sessions
are on the watch. There is deliberately no tool to abandon a plan on its own, no
tool to delete a planned workout, and no tool to delete an activity. Deleting a
run is the one irreversible operation and is never exposed. `call_api` is still
GET-only, so every write is reachable only through a named tool with its own
docstring. A write reports what actually changed, not what was asked: a plan
change whose watch push failed reads differently from one that succeeded, and
says the watch still holds the old version.

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

The nine plan writes, and why each exists:

- `set_workout_note(workout_id, note)` — replaces a planned session's
  `description`. The generator's easy-run text tells this athlete to take walk
  breaks, which cause him next-day pain, and nothing else could edit it.
- `move_workout(workout_id, date)` — moves one session. Reports whether the
  watch actually took the change, because a move whose re-push failed leaves
  the watch on the old day. The endpoint's `garmin_synced` is `False` both when
  Garmin rejected the push and when the session was deliberately taken off the
  watch (moved into the past, or became a rest day), so the tool reports the
  session's resulting state rather than reading that flag alone.
- `push_workout(workout_id)` — schedules one session on the watch. Separates
  "no structured steps, asking again will not help" (400) from "Garmin is
  down, try again" (502).
- `unpush_workout(workout_id)` — takes one session off the watch and leaves the
  plan untouched. It says so, because "remove" invites the reading that the
  session is deleted.
- `sync_plan_to_watch()` — pushes the whole active plan. Names each session that
  failed and why rather than a bare count, with the list bounded by
  construction.
- `push_adhoc_workout(name, date, steps)` — a one-off structured session with no
  plan behind it. Refuses a step whose pace bounds are missing or inverted,
  because `garmin_workout.py` would otherwise send it to the watch with no pace
  guard.
- `apply_suggestion(suggestion_id)` — applies one of the app's own suggestions. The server
  re-derives the list and refuses a stale id. It also **takes sessions off the
  watch**: a suggestion that changes a session's day type, distance or paces
  makes the watch's step list stale, and re-pushing would send the old steps
  (`_garmin_push` uploads only `w.structure`, the title and the date). Those
  sessions come off and the tool says which, rather than the watch holding a
  session that no longer matches the plan. This was chosen over leaving the
  watch stale, which is what the endpoint did before.
- `apply_calibration()` — re-aims the remaining easy, long and strides days at
  the measured easy pace and re-pushes them. The preview is read-only and
  appears in `get_plan_status`, so the athlete sees the change before agreeing.
- `create_plan(weeks, target_time_sec, start_date, replace_active)` — builds a new plan. Refuses while one is active
  unless `replace_active=True`, for the reason above.

**Verifying after a deploy:**
```bash
# an unknown token must 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://runflow-api.skdev.one/mcp/wrong/mcp
# a real token must answer a tools/call with 200 and a JSON-RPC result
curl -s -w "\nHTTP %{http_code}\n" -X POST "https://runflow-api.skdev.one/mcp/$TOKEN/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_records","arguments":{}}}'
```
The server is stateless, so there is no `initialize` handshake to make first and no
`mcp-session-id` header is ever sent; do not test for one. The second command must
print `HTTP 200` and a body containing `"result"`. A `GET` to the connector returns
`405 Method Not Allowed` by design (there is no event stream to offer), so a browser
visit or `curl` without `-X POST` is not a health check.

A `421 Misdirected Request` means the hostname is missing from `_ALLOWED_HOSTS` in
`backend/mcp_server.py`. The container log should say
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
