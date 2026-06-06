# TASKS 10: Server And Backend Container Metrics

## Goal

Add infrastructure metrics for the Lightsail host and the backend Docker
container, and expose them in a dedicated Grafana dashboard.

The existing observability stack only covers logs (Alloy -> Grafana Cloud Loki,
see `TASKS_5_LOGGING.md`). This task adds the metrics half that was listed there
as a follow-up: *"Add metrics later through Grafana Alloy."*

We want to see:

- per-core CPU usage, load average, and CPU spikes;
- RAM and swap usage;
- disk space and disk I/O;
- network throughput;
- backend container CPU, memory, network, and restarts.

## Decision

Extend the **existing** Alloy agent that already runs on the host. Use Alloy's
built-in exporters, no separate `node_exporter` or `cAdvisor` containers:

- `prometheus.exporter.unix` for host metrics;
- `prometheus.exporter.cadvisor` for Docker container metrics;
- `prometheus.remote_write` to Grafana Cloud **Prometheus** (a separate
  datasource and separate credentials from Loki).

Source of truth for the Alloy config stays **on the server**
(`/etc/alloy/config.alloy`), consistent with the decision recorded in
`TASKS_5_LOGGING.md`. We do not version the Alloy config in the repo. The
metrics block below is kept here only as a runbook.

The dashboard JSON is versioned in the repo and imported manually, exactly like
the logs dashboard.

Alerts are out of scope for this task.

## Server Setup (manual, not committed)

The Alloy config is not versioned in the repo. Append to
`/etc/alloy/config.alloy` on the Lightsail host, keeping the existing log
pipeline untouched:

```alloy
// ── Host metrics (node_exporter equivalent) ──
// Default Linux collectors: cpu, meminfo, loadavg, diskstats,
// filesystem, netdev, netstat, vmstat, pressure, time, etc.
prometheus.exporter.unix "host" { }

prometheus.scrape "host" {
  targets         = prometheus.exporter.unix.host.targets
  forward_to      = [prometheus.remote_write.grafana_cloud_prom.receiver]
  scrape_interval = "15s"
}

// ── Docker container metrics (cAdvisor) ──
prometheus.exporter.cadvisor "containers" {
  docker_host      = "unix:///var/run/docker.sock"
  storage_duration = "5m"
}

prometheus.scrape "containers" {
  targets         = prometheus.exporter.cadvisor.containers.targets
  forward_to      = [prometheus.remote_write.grafana_cloud_prom.receiver]
  scrape_interval = "15s"
}

// ── Ship metrics to Grafana Cloud Prometheus ──
prometheus.remote_write "grafana_cloud_prom" {
  endpoint {
    // Region eu-west-2 (same as Loki). Exact URL / username / token come from
    // Grafana Cloud -> Prometheus -> "Send Metrics" / Details.
    url = "https://prometheus-prod-XX-prod-eu-west-2.grafana.net/api/prom/push"
    basic_auth {
      username = "<PROM_INSTANCE_ID>"
      password = "<GRAFANA_CLOUD_TOKEN_metrics_write>"
    }
  }
  external_labels = {
    env  = "production",
    host = constants.hostname,
  }
}
```

Where to get the values:

- Grafana Cloud -> your stack -> Prometheus -> "Send Metrics" / Details.
- `username` is the Prometheus instance numeric ID.
- `password` is a Grafana Cloud token with the `metrics:write` scope.

Apply and verify:

```bash
alloy fmt /etc/alloy/config.alloy
sudo systemctl restart alloy
sudo systemctl status alloy
sudo journalctl -u alloy -n 100 --no-pager
```

Notes:

- cAdvisor needs Alloy access to `/var/run/docker.sock` (already granted for the
  log pipeline) and to host `/proc` and `/sys` (Alloy runs as a host systemd
  service, so it has access).
- The cAdvisor `name` label is the docker-compose container name (for example
  `backend_sloco-backend-1`), not `backend`. The dashboard filters by a
  `$container` template variable instead of hardcoding the name.

## Dashboard

Repo file:

```text
grafana/dashboards/server-metrics.json
```

Dashboard defaults:

- title: `Server & Backend Metrics`
- datasource: Prometheus (`grafanacloud-maevskyy-prom`), selected at import
- time range: last 3 hours
- refresh: 30 seconds
- tags: `server`, `metrics`, `prometheus`, `sloco`
- template variables: `instance`, `container`

Rows and panels:

- `At a glance` (stat): CPU total %, RAM used %, Disk used (/) %, host uptime,
  backend container up/down.
- `Host` (timeseries): CPU per core, load average, memory + swap, disk space per
  mount, disk I/O, network throughput.
- `Backend container` (timeseries): container CPU, memory working set, network,
  and container uptime (a drop signals a restart).

Import flow:

1. Grafana Cloud -> Dashboards -> New -> Import.
2. Paste `grafana/dashboards/server-metrics.json`.
3. Select the Prometheus datasource (`grafanacloud-maevskyy-prom`).
4. Save and open the dashboard.
5. Pick the real container in the `$container` dropdown if the default regex
   does not match.

## Test Plan

Local validation:

```bash
node -e "JSON.parse(require('fs').readFileSync('grafana/dashboards/server-metrics.json', 'utf8')); console.log('ok')"
```

Server validation:

- `alloy fmt` reports no syntax errors;
- `systemctl status alloy` is active after restart;
- `journalctl -u alloy` shows no remote_write auth errors.

Grafana validation:

- Explore -> Prometheus returns data for `node_load1`,
  `node_memory_MemAvailable_bytes`, and
  `container_cpu_usage_seconds_total{name=~".*backend.*"}`;
- dashboard imports, datasource picker appears, panels populate;
- a short load test (`stress-ng`, or a burst of curl to `/v1/map/places`) shows a
  visible CPU / network spike.

## Assumptions

- Grafana Cloud Prometheus is available on the existing stack.
- The Alloy version on the host supports `prometheus.exporter.unix` and
  `prometheus.exporter.cadvisor`.
- Manual import is enough for MVP.

## Security

- The current `/etc/alloy/config.alloy` stores the Loki token inline in
  plaintext. Rotate it in Grafana Cloud Access Policies.
- Keep the new Prometheus token server-side only. Do not commit tokens, push
  URLs, or instance IDs to the repo.

## Future Follow-Ups

- Alerts: CPU / RAM / disk thresholds and 5xx spike rules.
- Application metrics from Node/Fastify (event loop lag, RPS, latency
  histograms) via `prom-client` and a `/metrics` endpoint.
- Dashboard import automation (Grafana API or Terraform).
