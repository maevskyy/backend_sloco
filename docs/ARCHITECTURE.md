# Backend Architecture

The backend now runs as a small microservice stack on one host.

```text
public internet
  -> host Nginx / Certbot
  -> 127.0.0.1:3000
  -> backend container (gateway_service, Node/Fastify)
  -> http://recommendation-service:8000 over sloco_net
  -> recommendation-service container (Python/FastAPI)

Supabase managed Postgres stays outside the host and is accessed by the Gateway.
```

## Services

| Compose service | Code folder | Runtime | Port | Public |
| --- | --- | --- | --- | --- |
| `backend` | `gateway_service/` | Node 24, Fastify | `3000` | Yes, through Nginx |
| `recommendation-service` | `recommendation_service/` | Python 3.12, FastAPI | `8000` | No |
| `redis` | Docker image | Redis | `6379` | No, profile only |
| `grafana` | Docker image | Grafana | `3001 -> 3000` | Localhost only, profile only |

## Network Boundary

`docker-compose.yml` creates one private bridge network:

```text
sloco_net
```

The Gateway calls the recommendation service through:

```text
http://recommendation-service:8000
```

Do not call the recommendation service through the public domain. It is an
internal compute/runtime service.

## Public Routing

Host Nginx owns the public HTTP/HTTPS surface:

```text
/v1/* -> http://127.0.0.1:3000
/     -> ok
/*    -> closed
```

Certbot still owns the server-side HTTPS config. Nginx is not containerized in
this migration.

## Profiles

Default `docker compose up -d` starts only:

```text
backend
recommendation-service
```

Optional services:

```bash
docker compose --profile cache up -d
docker compose --profile observability up -d
docker compose --profile cache --profile observability up -d
```

Redis and Grafana are present for future work, not required for the current MVP
runtime.
