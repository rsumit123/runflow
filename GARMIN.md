# Garmin token runbook

RunFlow imports Garmin activities via a cached **token store** — no Garmin
password is ever stored on the server. The token (~1 year OAuth lifetime) is
generated locally and copied to the VM.

## First-time setup / refresh (~yearly, or if Garmin calls start returning 401)

1. Locally, in a venv with `garminconnect` installed, mint a token:
   ```bash
   python -c "from garminconnect import Garmin; import getpass; \
     g=Garmin(input('email: '), getpass.getpass('pw: '), prompt_mfa=lambda: input('mfa: ')); \
     g.login('/tmp/garmin_tokens'); print('ok')"
   ```

2. Copy the token directory to the VM's persistent data volume:
   ```bash
   rsync -avz /tmp/garmin_tokens/ ssh-social:/opt/runflow/data/garmin_tokens/
   ```

3. Restart the container so the app picks up the token:
   ```bash
   ssh ssh-social "sudo docker restart runflow-backend"
   ```

## How it wires together

- The container mounts `/opt/runflow/data` at `/data` (see `DEPLOYMENT.md`).
- `GARMIN_TOKENSTORE` defaults to `/data/garmin_tokens`, so the copied directory
  is found automatically. No extra env var is required, but you can override it
  by adding `-e GARMIN_TOKENSTORE=/data/garmin_tokens` to the `docker run` command.
- `backend/garmin_auth.py` loads the cached token on first use. If the directory
  is missing/empty, the `/api/import/garmin/sync` endpoint returns HTTP 400 with a
  message pointing back to this runbook.

## Usage

Trigger a sync from the frontend Import page ("Sync from Garmin"), or directly:
```bash
curl -X POST https://runflow-api.skdev.one/api/import/garmin/sync
```
It imports running activities not already stored (deduped by Garmin activity ID)
and stops once it reaches already-known activities.
