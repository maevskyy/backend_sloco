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
