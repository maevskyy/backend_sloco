# Backend Deployment

Production runs from one host directory:

```text
/opt/backend_sloco/docker-compose.yml
/opt/backend_sloco/.env
```

The host remains stateless for application data. Supabase managed Postgres is
still the database.

## Required Environment

`/opt/backend_sloco/.env` must include:

```bash
BACKEND_IMAGE=ghcr.io/maevskyy/backend_sloco:prod-latest
RECOMMENDATION_SERVICE_IMAGE=ghcr.io/maevskyy/recommender_sloco:prod-latest

NODE_ENV=production
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
RECOMMENDATION_SERVICE_URL=http://recommendation-service:8000

RECOMMENDATION_APP_ENV=production
RECOMMENDATION_LOG_LEVEL=info
```

Do not commit real secrets.

## Existing CD Compatibility

The existing service CD workflows keep working because the unified compose keeps
the same names they already use:

| Workflow | Updates env var | Runs compose service |
| --- | --- | --- |
| `gateway_service` CD | `BACKEND_IMAGE` | `backend` |
| `recommendation_service` CD | `RECOMMENDATION_SERVICE_IMAGE` | `recommendation-service` |

Each workflow builds and pushes its image to GHCR, updates the image tag in
`/opt/backend_sloco/.env`, and runs:

```bash
docker compose pull <service>
docker compose up -d <service>
```

The workflows do not copy the compose file. The unified compose must already be
present on the server.

## First Install / Compose Update

Copy the backend-level compose and Nginx template to the host manually or from a
future infra workflow:

```bash
scp docker-compose.yml <user>@<host>:/opt/backend_sloco/docker-compose.yml
scp deploy/nginx/backend_sloco.conf <user>@<host>:/tmp/backend_sloco.conf
```

On the host, validate before starting:

```bash
cd /opt/backend_sloco
grep -E '^(BACKEND_IMAGE|RECOMMENDATION_SERVICE_IMAGE)=' .env
docker compose -f docker-compose.yml config
```

Then start the default stack:

```bash
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
docker compose -f docker-compose.yml ps
```

## Health Checks

Gateway:

```bash
curl http://127.0.0.1:3000/v1/health
curl https://sloco.pp.ua/v1/health
```

Recommendation service from inside the network:

```bash
docker compose exec backend node -e "fetch('http://recommendation-service:8000/v1/health/ready').then(r=>r.text()).then(console.log)"
```

## Rollback

Because each service image tag is stored in `.env`, rollback is:

1. Put the previous image tag back into `BACKEND_IMAGE` or
   `RECOMMENDATION_SERVICE_IMAGE`.
2. Run `docker compose pull <service>`.
3. Run `docker compose up -d <service>`.
4. Check service health and logs.

## Logs

Local Docker logs are bounded by compose log rotation.

```bash
docker compose logs --tail=100 backend
docker compose logs --tail=100 recommendation-service
docker compose logs -f backend
```
