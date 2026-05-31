# Task Plans

This folder indexes implementation plans.

Task files live here as `TASKS_N_NAME.md`.

## Existing Tasks

| Status | File | Summary |
| --- | --- | --- |
| Done | `TASKS_1_CI.md` | CI with parallel build, test, lint jobs. |
| Done | `TASKS_2_CD.md` | Manual production deploy, originally to Lightsail. Current deploy target is Hetzner. |
| Done | `TASKS_3_DB.md` | Supabase foundation and first raw TripAdvisor table. Historical; current serving table is `places`. |
| Done | `TASKS_4_FIRST_ENDPOINT.md` | First map endpoint for frontend. Historical shape. |
| Done | `TASKS_5_LOGGING.md` | Grafana Cloud logging setup. Historical Lightsail setup. |
| Done | `TASKS_6_POLISHED_LOGS.md` | Structured backend logging. |
| Done | `TASKS_7_GRAFANA_DASHBOARD_LOGS.md` | Grafana logs dashboard. |
| Done | `TASKS_8_REPO_REFACTORING.md` | Repo structure cleanup plan. |
| Done | `TASKS_9_SWAGGER.md` | Swagger/OpenAPI contract for frontend. |
| Done | `TASKS_10_SERVER_METRICS.md` | Server and backend container metrics dashboard. Historical host setup. |
| Done | `TASKS_11_DB_PLACES.md` | Unified source-agnostic `places` table. |
| Done | `TASKS_12_INTEGRATION_MAPPERS.md` | Per-source mappers into the `places` import format. |
| Done | `TASKS_13_MAP_DENSITY_RANKING.md` | Zoom-based map density and ranking. |
| Done | `TASKS_14_MAP_BBOX_ONLY.md` | Drop required `city`, make the map endpoint bbox-only. |
| Done | `TASKS_15_SERVER_MIGRATION.md` | Minimal Hetzner deploy migration runbook. |
| Done | `TASKS_16_SUPABASE_AUTH_FOUNDATION.md` | Supabase Auth JWT validation and `/v1/me` backend foundation. |
| Done | `TASKS_17_SAVED_PLACES.md` | Saved places + collections frontend contract rework. |

## TBD Backlog

- `TBD_DOMAIN_HTTPS_NGINX_HARDENING.md` - future domain, HTTPS, and Nginx hardening.
- `TBD_SELF_HOST_OBSERVABILITY.md` - future move from Grafana Cloud to self-hosted Grafana, Loki, and Prometheus.

## Naming Rule

New task files should use:

```text
TASKS_N_SHORT_NAME.md
```

Keep task docs decision-oriented:

- what changes;
- why;
- files or areas involved;
- test plan;
- assumptions.

Do not use task docs as permanent product docs. Once a task creates lasting
behavior, link or summarize that behavior from `docs/CURRENT_STATE.md`,
`docs/DECISIONS.md`, `docs/README.md`, `README.md`, or a runbook.
