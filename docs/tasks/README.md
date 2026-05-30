# Task Plans

This folder indexes implementation plans.

Task files live here as `TASKS_N_NAME.md`.

## Existing Tasks

- `TASKS_1_CI.md` - CI with parallel build, test, lint jobs.
- `TASKS_2_CD.md` - manual production deploy to Lightsail.
- `TASKS_3_DB.md` - Supabase foundation and raw TripAdvisor table.
- `TASKS_4_FIRST_ENDPOINT.md` - first map endpoint for frontend.
- `TASKS_5_LOGGING.md` - Grafana Cloud logging setup.
- `TASKS_6_POLISHED_LOGS.md` - structured backend logging.
- `TASKS_7_GRAFANA_DASHBOARD_LOGS.md` - Grafana logs dashboard.
- `TASKS_8_REPO_REFACTORING.md` - repo structure cleanup plan.
- `TASKS_9_SWAGGER.md` - Swagger/OpenAPI contract for frontend.
- `TASKS_10_SERVER_METRICS.md` - server and backend container metrics dashboard.
- `TASKS_11_DB_PLACES.md` - unified source-agnostic `places` table.
- `TASKS_12_INTEGRATION_MAPPERS.md` - per-source mappers into the `places` import format.
- `TASKS_13_MAP_DENSITY_RANKING.md` - zoom-based map density and ranking.
- `TASKS_14_MAP_BBOX_ONLY.md` - drop required `city`, make the map endpoint bbox-only.
- `TASKS_15_SERVER_MIGRATION.md` - minimal Hetzner deploy migration runbook.

## TBD Backlog

- `TBD_DOMAIN_HTTPS_NGINX_HARDENING.md` - future domain, HTTPS, and Nginx hardening.

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
behavior, link or summarize that behavior from `docs/README.md`, `README.md`, or
a runbook.
