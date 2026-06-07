# Backend Architecture

This repo is the backend monorepo for Sloco. It owns the application services,
runtime compose stack, deploy configs, load tests, and backend operations docs.

## Runtime Shape

```text
public internet
  -> host Nginx / Certbot
  -> 127.0.0.1:3000
  -> backend container (services/gateway, Node/Fastify)
  -> http://recommendation-service:8000 over sloco_net
  -> recommendation-service container (services/recommendation, Python/FastAPI)

Gateway also talks to:
  -> Supabase managed Postgres / Auth / Storage
  -> Redis over sloco_net for hot read cache

Observability:
  -> Alloy host agent
  -> local Loki / Prometheus / Grafana stack
```

Supabase remains managed outside the host. The Hetzner box should be replaceable:
runtime state is either in Docker named volumes for infra tools or in managed
services.

## Repository Layout

```text
services/
  gateway/          public API Gateway, Fastify, Supabase stores, OpenAPI
  recommendation/   private recommendation runtime, FastAPI

observability/      monitoring stack config (own concern)
  grafana/          provisioning + dashboards (dashboards/{app,infra})
  prometheus/       prometheus.yml + rules/ (alerts later)
  loki/             loki config

deploy/
  nginx/            host Nginx templates (public edge)

load/               Artillery scenarios
docs/               backend-platform docs and task plans
docker-compose.yml  production stack source of truth
```

Service-local docs stay inside the service folder. Cross-service runtime,
deployment, observability, and operations docs live in root `docs/`.

## Services

| Compose service | Code/config folder | Runtime | Port | Public |
| --- | --- | --- | --- | --- |
| `backend` | `services/gateway/` | Node 24, Fastify | `127.0.0.1:3000` | Yes, through Nginx |
| `recommendation-service` | `services/recommendation/` | Python 3.12, FastAPI | `8000` on Docker network | No |
| `redis` | Docker image | Redis | `6379` on Docker network | No |
| `loki` | `observability/loki/` | Loki | `127.0.0.1:3100` | No |
| `prometheus` | `observability/prometheus/` | Prometheus | `127.0.0.1:9090` | No |
| `grafana` | `observability/grafana/` | Grafana | `127.0.0.1:3001` | Via `grafana.sloco.pp.ua` |

## Network Boundary

`docker-compose.yml` creates one private bridge network:

```text
sloco_net
```

The Gateway calls internal services by compose DNS:

```text
http://recommendation-service:8000
redis://redis:6379/0
```

Do not call private services through public domains. Public access enters through
Nginx only.

## Public Routing

Host Nginx owns public HTTP/HTTPS:

```text
sloco.pp.ua/v1/*          -> http://127.0.0.1:3000
grafana.sloco.pp.ua/*     -> http://127.0.0.1:3001
```

Certbot owns HTTPS certificates and server-side `listen 443 ssl` blocks. Nginx
is host-level, not containerized.

## Deployment Ownership

`.github/workflows/deploy-production.yml` owns production convergence:

1. verify selected service checks;
2. build and push selected service images;
3. sync `docker-compose.yml`, `deploy/**`, and `observability/**`;
4. render `/opt/backend_sloco/.env` from GitHub secrets;
5. run `docker compose up -d` (app only). The self-hosted observability stack
   (Loki/Prometheus/Grafana) is opt-in via the `with_observability` input — only
   then does it render `GF_*` and run `--profile observability`.

No production compose/config file should be copied by hand during normal deploys.

## What Belongs Where

- New public API behavior: `services/gateway/src/modules/`.
- New private algorithm runtime: `services/<new-service>/`.
- Runtime stack wiring: root `docker-compose.yml`.
- Host Nginx templates: `deploy/nginx/`.
- Monitoring stack config (Loki/Prometheus/Grafana, provisioning, alert rules):
  `observability/`.
- Dashboard JSON: `observability/grafana/dashboards/{app,infra}/`.
- Load scenarios: `load/`.
- Cross-service docs: root `docs/`.
