# Deployment

Production runs on a single Ubuntu host with Docker Compose and Nginx. The host
is **stateless**: the database is managed Supabase, so the box holds no
irreplaceable application data.

For standing up a new host from scratch, see
`docs/tasks/TASKS_15_SERVER_MIGRATION.md`.

## Server Directory

```text
/opt/backend_sloco/docker-compose.yml   (from deploy/docker-compose.production.yml)
/opt/backend_sloco/.env                  (secrets, created by hand)
```

## Environment

Create `/opt/backend_sloco/.env` by hand:

```bash
NODE_ENV=production
HOST=0.0.0.0
PORT=3000
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Do not commit production secrets. `SUPABASE_SERVICE_ROLE_KEY` must stay
server-side only — never expose it to the iOS app or any public frontend.

## Nginx

`deploy/nginx/backend_sloco.conf` is the domain HTTP template, installed to
`/etc/nginx/sites-available/backend_sloco`. It proxies public `/v1/*` traffic to:

```text
http://127.0.0.1:3000
```

Production domain:

```text
https://sloco.pp.ua
```

HTTPS is managed on the server by Certbot + Let's Encrypt. Certbot owns the
server-side `listen 443 ssl` blocks and certificate paths under
`/etc/letsencrypt`.

Nginx should block random non-API paths before they reach Fastify:

```text
/v1/* -> backend container
/     -> simple ok response
/*    -> 444 closed connection
```

## Logs

Docker log rotation is set in the compose template (`json-file`, `max-size 10m`,
`max-file 3`), bounding local logs to ~30 MB per container.

Useful server commands:

```bash
cd /opt/backend_sloco
docker compose logs --tail=100 backend
docker compose logs -f backend
```

## Manual Deploy Workflow

GitHub Actions workflow:

```text
.github/workflows/deploy-production.yml
```

It is manual only (`workflow_dispatch`) and accepts a branch, tag, or commit SHA.
It builds + pushes the image to GHCR, SSH-deploys to the host, and healthchecks.

Required GitHub repository secrets:

```text
DEPLOY_HOST          # server IP / host
DEPLOY_USER          # ssh user (e.g. ubuntu)
DEPLOY_SSH_KEY       # ssh private key
PRODUCTION_API_URL   # https://sloco.pp.ua for the remote healthcheck
```

Optional if the server must authenticate to pull from GHCR:

```text
GHCR_USERNAME
GHCR_READ_TOKEN
```
