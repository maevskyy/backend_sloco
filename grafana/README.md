# Grafana

This folder stores Grafana dashboard-as-code files for the backend.

## Current Dashboards

```text
dashboards/backend-logs.json
```

Purpose:

- view backend logs from Grafana Cloud Loki;
- show live logs, request/response logs, map endpoint logs, healthchecks,
  errors, bad requests, and slow requests.

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

5. Save the dashboard.

## Update Flow

When dashboard JSON changes:

1. Copy the full updated JSON.
2. Open the dashboard in Grafana.
3. Paste JSON through import/editor flow.
4. Apply changes.
5. Save dashboard.
6. Generate traffic and verify panels.

Example traffic:

```bash
curl http://52.18.13.69/v1/health
curl "http://52.18.13.69/v1/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
```

## Safety Rules

- Do not commit Grafana Cloud API tokens.
- Do not commit Loki push URLs.
- Do not commit datasource passwords.
- Dashboard JSON should use datasource import variables or non-secret datasource
  names only.

## Current Labels

Expected Loki label:

```logql
{service="backend"}
```

Alloy adds production labels such as:

- `service=backend`
- `env=production`
- `container=...`
- `host=...`
