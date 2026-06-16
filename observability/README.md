# Observability

Self-hosted monitoring stack config for the backend monorepo: Grafana (provisioning
+ dashboards), Prometheus, Loki, and (later) Alertmanager. This is a top-level
concern, not owned by any single service.

```text
grafana/
  provisioning/datasources/datasources.yml   Loki + Prometheus (UIDs loki/prometheus)
  provisioning/dashboards/dashboards.yml       file provider (foldersFromFilesStructure)
  dashboards/
    app/    backend-logs.json, backend-metrics.json   (application telemetry, Loki)
    infra/  server-metrics.json                        (host/container metrics, Prometheus)
    load/   load-test.json                             (load/stress dashboard, Prometheus)
prometheus/
  prometheus.yml                                  scrapes: gateway, redis, cadvisor, node (+ supabase, optional)
  rules/                                         (alert/recording rules — later)
loki/
  loki-config.yml
```

Purpose:

- `backend-logs.json` (Loki): view backend logs — live
  logs, request/response logs, map endpoint logs, healthchecks, errors, bad
  requests, and slow requests.
- `backend-metrics.json` (Loki): backend metric logs — HTTP latency, dependency
  latency, cache events, slow calls, and large responses.
- `server-metrics.json` (Prometheus): host and backend container metrics — CPU
  per core, load, RAM/swap, disk, network, and container CPU/memory/restarts.
- `load-test.json` (Prometheus): the load/stress dashboard. Read it **top-down to
  find what broke and when** — every row shares one time axis:
  1. **Verdict** — RPS, error rate (5xx/4xx), HTTP latency p50/p95/p99. When the
     latency cliffs or errors start = the moment it broke.
  2. **Gateway/Node** — event-loop lag p99 (the silent Node killer), RSS/heap, CPU.
  3. **Dependencies** — Supabase/Redis/ML latency p95 + error rate as seen by the
     gateway, plus cache hit/miss. (Supabase latency/errors are visible here even
     without scraping Supabase directly.)
  4. **Redis** — used vs max memory, evictions, ops, clients, hit ratio.
  5. **Containers** — per-container CPU, memory, CPU throttling.
  Method: find the break in row 1, then read the cause in the same timestamp below
  (Supabase latency spike / Redis evictions / container CPU throttle / event-loop
  lag). Client-side truth (RPS sent, client-observed latency) lives in Artillery
  Cloud — align by wall-clock.

Real Prometheus metrics come from the gateway `/metrics` endpoint (prom-client:
RED histograms + Node runtime + dependency/cache) and from the `redis_exporter`,
`cadvisor`, and `node_exporter` containers (observability profile). The older
Loki-based `backend-metrics.json` stays for detailed traces/debugging.

## Datasource Per Dashboard

Datasources are provisioned by `provisioning/datasources/datasources.yml`:

- `Loki` with UID `loki`.
- `Prometheus` with UID `prometheus`.

Dashboard JSON files use those fixed UIDs and no longer prompt for datasource
selection at import time.

The metrics dashboard needs the Alloy metrics pipeline running on the host. See
`docs/tasks/TASKS_10_SERVER_METRICS.md` for the server-side Alloy setup.

## Provisioning Flow

The self-hosted Grafana service mounts the `grafana/` subfolder:

```yaml
./observability/grafana/dashboards:/var/lib/grafana/dashboards:ro
./observability/grafana/provisioning:/etc/grafana/provisioning:ro
```

On Grafana start, datasources are created and the on-disk subfolders (`app`, `infra`)
become Grafana dashboard folders automatically. No manual import is needed.

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
