# Grafana

This folder stores provisioned Grafana dashboard-as-code files for the backend.

## Current Dashboards

```text
dashboards/backend-logs.json
dashboards/backend-metrics.json
dashboards/server-metrics.json
provisioning/datasources/datasources.yml
provisioning/dashboards/dashboards.yml
```

Purpose:

- `backend-logs.json` (Loki): view backend logs — live
  logs, request/response logs, map endpoint logs, healthchecks, errors, bad
  requests, and slow requests.
- `backend-metrics.json` (Loki): backend metric logs — HTTP latency, dependency
  latency, cache events, slow calls, and large responses.
- `server-metrics.json` (Prometheus): host and backend container metrics — CPU
  per core, load, RAM/swap, disk, network, and container CPU/memory/restarts.

## Datasource Per Dashboard

Datasources are provisioned by `provisioning/datasources/datasources.yml`:

- `Loki` with UID `loki`.
- `Prometheus` with UID `prometheus`.

Dashboard JSON files use those fixed UIDs and no longer prompt for datasource
selection at import time.

The metrics dashboard needs the Alloy metrics pipeline running on the host. See
`docs/tasks/TASKS_10_SERVER_METRICS.md` for the server-side Alloy setup.

## Provisioning Flow

The self-hosted Grafana service mounts this folder:

```yaml
./services/gateway/grafana/dashboards:/var/lib/grafana/dashboards:ro
./services/gateway/grafana/provisioning:/etc/grafana/provisioning:ro
```

On Grafana start, datasources and the `Sloco` dashboard folder are created
automatically. No manual import is needed.

## Update Flow

When dashboard JSON changes:

1. Edit the dashboard JSON in `dashboards/`.
2. Validate it with `node -e "JSON.parse(require('fs').readFileSync(...))"`.
3. Restart Grafana or wait for the provider refresh interval.
4. Generate traffic and verify panels.

Example traffic:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

## Safety Rules

- Do not commit Grafana Cloud API tokens.
- Do not commit Loki push URLs.
- Do not commit datasource passwords.
- Dashboard JSON should use provisioned datasource UIDs only: `loki` and
  `prometheus`.

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
