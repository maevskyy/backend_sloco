# TASKS 1: Microservices Infra Foundation

**Status: Done.**

## Context

The backend is moving from one Node/Fastify monolith to a small microservice
stack:

- `gateway_service` is the public API Gateway, still serving `https://sloco.pp.ua`.
- `recommendation_service` is a private Python/FastAPI service for future
  recommendation workloads.

Both services already have independent Dockerfiles and CD workflows. The missing
piece is the backend-level orchestration layer: one compose file, one private
network, one deploy directory, and shared documentation.

## Goals

- Keep the existing CD workflows working without changes.
- Preserve the current public API surface: Nginx proxies `/v1/*` to the gateway.
- Run `recommendation_service` only on the private Docker network.
- Give the gateway a typed HTTP client wrapper for future recommendation calls.
- Keep Redis and local Grafana available behind compose profiles, not enabled by
  default.

## Non-Goals

- Do not add Kafka, RabbitMQ, or async messaging.
- Do not expose the recommendation service publicly.
- Do not implement recommendation algorithms in this task.
- Do not containerize host Nginx or move Certbot into Docker.

## Target Shape

```text
backend/
  docker-compose.yml
  docker-compose.override.yml
  .env.example
  Makefile
  deploy/nginx/backend_sloco.conf
  docs/
    README.md
    ARCHITECTURE.md
    DEPLOYMENT.md
    tasks/TASKS_1_MICROSERVICES_INFRA.md
  gateway_service/
  recommendation_service/
```

## Checklist

- [x] Add backend-level `.gitignore`.
- [x] Add backend-level `.env.example`.
- [x] Add unified `docker-compose.yml`.
- [x] Add local `docker-compose.override.yml`.
- [x] Add backend-level Makefile.
- [x] Copy canonical Nginx template to `backend/deploy/nginx/`.
- [x] Add backend-level docs.
- [x] Add gateway env setting for `RECOMMENDATION_SERVICE_URL`.
- [x] Add gateway recommendation service HTTP client wrapper.
- [x] Update gateway agent guidance for the microservices pivot.
- [x] Verify compose config and gateway checks.
- [x] Initialize the backend infra git repo.

## Verification Result

Passed:

```bash
docker compose config
docker compose -f docker-compose.yml config
docker compose up --build -d
docker compose ps
docker compose exec backend node -e "fetch('http://recommendation-service:8000/v1/health/ready').then(r=>r.text()).then(console.log)"
curl http://127.0.0.1:3000/v1/health
cd gateway_service && pnpm build
cd gateway_service && pnpm test
cd gateway_service && pnpm typecheck
cd gateway_service && pnpm lint
```

Note: command output includes a local `pyenv` warning about shims not being
writable. The checks themselves passed.

## Verification Plan

```bash
cd backend
docker compose config
docker compose -f docker-compose.yml config
```

Local runtime check:

```bash
docker compose up --build -d
docker compose ps
docker compose exec backend node -e "fetch('http://recommendation-service:8000/v1/health/ready').then(r=>r.text()).then(console.log)"
curl http://127.0.0.1:3000/v1/health
```

Gateway checks:

```bash
cd backend/gateway_service
pnpm build
pnpm test
pnpm typecheck
pnpm lint
```
