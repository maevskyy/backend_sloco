# Backend Docs

This folder documents the backend monorepo as a multi-service system.

## Start Here

- `ARCHITECTURE.md` - monorepo layout, service map, network boundaries, ports,
  and ownership rules.
- `CURRENT_STATE.md` - what is done, what is not done, and the current platform
  score.
- `DEPLOYMENT.md` - production deploy workflow, secrets, server layout, and
  rollback.
- `tasks/` - backend-platform task plans and migration history.
- `tasks/TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md` - consolidate the three backend
  repos into one monorepo, with path-filtered CI, stack-owning deploy, one secret
  contour, and a load-testing harness.
- `tasks/TASKS_3_OBSERVABILITY_TOPLEVEL.md` - move Grafana/Prometheus/Loki config to a
  top-level `observability/` directory with dashboards grouped by `app/`/`infra/`.

## Cross-Service Working Docs

Root-level docs that span both services (they are not service docs and not task plans):

- `../ios-asks-implementation-plan.md` - the 2026-08 round of iOS asks: spec-vs-code
  comparison, per-ask plan, and the ship ritual. All nine asks are shipped; kept as the
  record of what was decided and why.
- `../recommender-config-audit.md` - recommendation-service configuration audit
  (algorithm/artifact pairing). P0 items resolved 2026-08-11.
- `../user-reactions-spec.md` - the cross-service user-reactions contract (gateway
  `TASKS_34`/`35` + recommendation `TASKS_3`).

## TBD Thinking Docs

- `tasks/TBD_PLATFORM_HARDENING.md` - whole-repo backlog of known gaps/concerns
  (load testing, alerting, staging, DR/backups, algorithm pipeline, rate limiting)
  with the current architecture snapshot and priority order.
- `tasks/TBD_CICD_SECRETS_AND_RUNNERS.md` - CI/CD, secrets, private repos, and
  self-hosted runners. Largely closed by `tasks/TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md`
  (kept for rationale/history).

## Service Docs

- `../services/gateway/README.md` - API Gateway local development and service notes.
- `../services/gateway/AGENTS.md` - Gateway coding conventions and module rules.
- `../services/gateway/docs/README.md` - Gateway API docs and historical
  Gateway task plans.
- `../services/recommendation/README.md` - Recommendation service local
  development and health endpoints.

## Source Of Truth

- The public API boundary is the Gateway.
- The production compose source of truth is `../docker-compose.yml`.
- The production deploy source of truth is `../.github/workflows/deploy-production.yml`.
- Nginx remains host-level and proxies public `/v1/*` traffic to the Gateway.
- The recommendation service is private and is reached through the Docker
  network.
- Redis is a normal runtime dependency for Gateway cache.
- Grafana/Loki/Prometheus are self-hosted through the root compose observability
  profile and provisioned from service dashboard files.
