# Backend Current State

Last updated: 2026-06-07.

## What This Backend Is Now

```text
Repository model: backend monorepo
Runtime model: single-host Docker Compose service stack
Gateway model: modular API Gateway
Private compute: Python recommendation service
Observability: self-hosted Grafana + Loki + Prometheus
Database/Auth/Storage: managed Supabase
```

## Done

- Backend repos were consolidated into one monorepo.
- Service code lives under `services/`:
  - `services/gateway` — public Fastify API Gateway.
  - `services/recommendation` — private FastAPI recommendation service.
- Root `docker-compose.yml` owns the production runtime stack.
- Root `.github/workflows/deploy-production.yml` owns production deploy.
- Deploy workflow was tested on production and ships stack files automatically.
- Production `.env` is rendered from GitHub secrets by CI/CD.
- Redis is part of the runtime stack and is used for place-details cache.
- Self-hosted Grafana/Loki/Prometheus are deployed and provisioned from repo files.
- Grafana dashboards load from `services/gateway/grafana/`.
- Load-test harness exists in `load/`.
- Root docs now own platform architecture/deployment/hardening state.

## Not Done Yet

- Load baseline is not measured yet; SLOs in `load/README.md` are placeholders.
- Alerting is not configured.
- There is no staging environment.
- Rollback is documented but has not been drilled.
- Grafana/Loki/Prometheus volume backup and retention behavior are not proven.
- Recommendation embedding generation is not reproducible yet.
- Gateway rate limiting is not implemented.
- Supabase import/migration process still needs more operational discipline.
- Final Grafana Cloud token/account deletion should be confirmed if not already done.

## Current Operational Score

Approximate backend-platform score for a solo MVP: **8.7 / 10**.

The shape is now strong. Remaining work is mostly proving and hardening:
load, alerts, rollback, staging, backups, and data/algorithm pipelines.

## Source Of Truth

- Architecture: `docs/ARCHITECTURE.md`
- Deployment: `docs/DEPLOYMENT.md`
- Known gaps: `docs/tasks/TBD_PLATFORM_HARDENING.md`
- Platform tasks: `docs/tasks/README.md`
- Gateway docs: `services/gateway/docs/README.md`
- Recommendation docs: `services/recommendation/README.md`
