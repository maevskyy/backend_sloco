# Backend Docs

This folder documents the backend as a multi-service system.

## Start Here

- `ARCHITECTURE.md` - service map, network boundaries, ports, and request flow.
- `DEPLOYMENT.md` - production layout under `/opt/backend_sloco` and CD roles.
- `tasks/` - backend-level task plans and migration history.
- `tasks/TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md` - consolidate the three backend
  repos into one monorepo, with path-filtered CI, stack-owning deploy, one secret
  contour, and a load-testing harness.

## TBD Thinking Docs

- `tasks/TBD_CICD_SECRETS_AND_RUNNERS.md` - CI/CD, secrets, private repos, and
  self-hosted runners. Largely closed by `tasks/TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md`
  (kept for rationale/history).

## Service Docs

- `../services/gateway/README.md` - API Gateway local development and API notes.
- `../services/gateway/AGENTS.md` - Gateway coding conventions.
- `../services/recommendation/README.md` - Recommendation service local
  development and health endpoints.

## Source Of Truth

- The public API boundary is the Gateway.
- The production compose source of truth is `../docker-compose.yml`.
- Nginx remains host-level and proxies public `/v1/*` traffic to the Gateway.
- The recommendation service is private and is reached through the Docker
  network.
