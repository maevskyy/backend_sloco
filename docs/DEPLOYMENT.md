# Deployment

Production runs on AWS Lightsail with Docker Compose and Nginx.

## Server Directory

```text
/opt/backend_sloco
```

Expected files:

```text
/opt/backend_sloco/docker-compose.yml
/opt/backend_sloco/.env
```

Use `deploy/docker-compose.production.yml` as the production compose template.

## Environment

Example `/opt/backend_sloco/.env`:

```bash
NODE_ENV=production
HOST=0.0.0.0
PORT=3000
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

Do not commit production secrets.

`SUPABASE_SERVICE_ROLE_KEY` must stay server-side only. Do not expose it to the
iOS app or any public frontend.

## Nginx

Use `deploy/nginx/backend_sloco.conf` as the initial HTTP-only Nginx template.

It proxies public traffic to:

```text
http://127.0.0.1:3000
```

Add HTTPS with Certbot after a domain is ready.

## Logs

Production logs are written by the backend container to stdout/stderr.

On the server, Docker log rotation should be enabled in:

```text
/opt/backend_sloco/docker-compose.yml
```

Expected logging config:

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

This keeps local Docker logs bounded to roughly 30 MB for the backend
container.

Grafana Alloy runs on the Lightsail host and ships Docker logs to Grafana Cloud
Loki.

Useful server commands:

```bash
cd /opt/backend_sloco
docker compose logs --tail=100 backend
docker compose logs -f backend
sudo systemctl status alloy
sudo journalctl -u alloy -n 100 --no-pager
```

Useful Grafana Loki queries:

```logql
{service="backend"}
```

```logql
{service="backend"} | json | path != ""
```

```logql
{service="backend"} | json | path = "/map/places"
```

```logql
{service="backend"} | json | level = "error"
```

Request completion logs include `method`, `url`, `path`, `statusCode`,
`responseTimeMs`, and `reqId`. In production they stay as structured JSON so
Grafana can filter them; in local development they are colorized with
`pino-pretty`.

## Manual Deploy Workflow

GitHub Actions workflow:

```text
.github/workflows/deploy-production.yml
```

It is manual only and accepts a branch, tag, or commit SHA.

Required GitHub repository secrets:

```text
LIGHTSAIL_HOST
LIGHTSAIL_USER
LIGHTSAIL_SSH_KEY
PRODUCTION_API_URL
```

Optional if the server must authenticate to pull from GHCR:

```text
GHCR_USERNAME
GHCR_READ_TOKEN
```
