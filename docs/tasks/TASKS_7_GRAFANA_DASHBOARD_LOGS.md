# TASKS 7: Grafana Logs Dashboard

## Summary

Create a Grafana Cloud logs dashboard for the backend, stored in the repo as
dashboard JSON.

The dashboard is meant to be the default place for MVP debugging, instead of
forcing the team to use raw Grafana Explore every time.

We keep this simple:

- dashboard JSON lives in the repo;
- import is manual through Grafana UI;
- Loki datasource is selected during import;
- no Grafana tokens, URLs, or secrets are committed.

## Decision

Use Grafana dashboard JSON:

```text
grafana/dashboards/backend-logs.json
```

Do not use YAML provisioning for MVP.

YAML provisioning is mostly useful for self-hosted Grafana. Since we use
Grafana Cloud, the simple path is:

```text
repo JSON -> Grafana UI import -> select Loki datasource -> save dashboard
```

Later, if dashboard automation becomes important, we can add one of:

- Grafana HTTP API;
- Grafana Terraform provider;
- CI job that uploads dashboards.

## Dashboard Behavior

Dashboard defaults:

- title: `Backend Logs`
- time range: last 1 hour
- refresh: 10 seconds
- timezone: browser
- tags: `backend`, `logs`, `loki`, `sloco`
- layout: full-width log panels stacked vertically
- log rendering: compact one-line logs, no JSON prettify, no wrapping
- JSON compatibility fields: `preload`, full `timeSettings`, `variables`

Datasource:

- type: Loki
- selected during import
- expected datasource: `grafanacloud-maevskyy-logs`

Base LogQL label:

```logql
{service="backend"}
```

## Panels

The dashboard contains:

- `Live Backend Logs`
  ```logql
  {service="backend"} | json
  ```

- `Request / Response Logs`
  ```logql
  {service="backend"} | json | path != ""
  ```

- `Map Endpoint`
  ```logql
  {service="backend"} | json | path = "/map/places"
  ```

- `Healthchecks`
  ```logql
  {service="backend"} | json | path =~ "/health.*"
  ```

- `Errors`
  ```logql
  {service="backend"} | json | statusCode >= 500
  ```

- `Bad Requests`
  ```logql
  {service="backend"} | json | statusCode >= 400 | statusCode < 500
  ```

- `Slow Requests`
  ```logql
  {service="backend"} | json | responseTimeMs > 500
  ```

These queries assume the polished log shape from `TASKS_6_POLISHED_LOGS.md`.

## Import Flow

1. Open Grafana Cloud.
2. Go to:

   ```text
   Dashboards -> New -> Import
   ```

3. Upload or paste:

   ```text
   grafana/dashboards/backend-logs.json
   ```

4. Select Loki datasource:

   ```text
   grafanacloud-maevskyy-logs
   ```

5. Save dashboard.
6. Open the dashboard.
7. Generate traffic:

   ```bash
   curl http://52.18.13.69/health
   curl http://52.18.13.69/health/supabase
   curl "http://52.18.13.69/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
   ```

8. Confirm new logs appear in the dashboard.

## Test Plan

Local validation:

```bash
node -e "JSON.parse(require('fs').readFileSync('grafana/dashboards/backend-logs.json', 'utf8')); console.log('ok')"
```

Manual Grafana validation:

- dashboard imports successfully;
- datasource selector appears during import;
- `Live Backend Logs` shows recent logs;
- `/health`, `/health/supabase`, and `/map/places` appear after curl traffic;
- error panels show existing 4xx/5xx logs or remain empty without breaking;
- no secrets are present in the dashboard JSON.

## Assumptions

- Grafana Cloud Loki datasource already exists.
- Alloy ships Docker logs with `service="backend"`.
- Backend request logs follow the polished shape from task 6.
- Manual import is enough for MVP.
