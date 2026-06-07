# Backend Platform Tasks

This folder tracks backend-platform tasks: repository topology, deploy, CI/CD,
secrets, load testing, and platform hardening.

Gateway product/API tasks live in:

```text
../../services/gateway/docs/tasks/
```

## Current Status

| Status | File | Summary |
| --- | --- | --- |
| Done | `TASKS_1_MICROSERVICES_INFRA.md` | Historical first service split: Gateway + Recommendation on one Compose stack. Superseded by the monorepo shape, but useful history. |
| Done | `TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md` | Consolidated backend repos into one monorepo, added path-filtered CI, stack-owning deploy, one secret contour, and load harness. Production deploy has been tested. |
| Backlog | `TBD_PLATFORM_HARDENING.md` | Known gaps after the platform became operational: load baseline, alerting, staging, rollback drill, backups, algorithm pipeline, security hardening. |
| Mostly Closed | `TBD_CICD_SECRETS_AND_RUNNERS.md` | Historical CI/CD/secrets thinking doc. Mostly closed by `TASKS_2`; kept for rationale and future private-runner/secrets-manager decisions. |

## Done

- Backend is one monorepo.
- `services/gateway` is the public API Gateway.
- `services/recommendation` is the private Python recommendation service.
- Root `docker-compose.yml` owns the runtime stack.
- Root `.github/workflows/deploy-production.yml` owns production deploy.
- CI/CD ships stack files and renders `/opt/backend_sloco/.env`.
- Redis cache is part of the runtime stack.
- Self-hosted Grafana/Loki/Prometheus are deployed through the monorepo workflow.
- Load-test harness exists in `load/`.

## Not Done Yet

- Real load baseline and SLO calibration.
- Alerting.
- Staging.
- Rollback drill.
- Observability backups/retention validation.
- Reproducible embedding pipeline.
- Rate limiting and security hardening.
- Final Grafana Cloud account/token cleanup if not already completed.
