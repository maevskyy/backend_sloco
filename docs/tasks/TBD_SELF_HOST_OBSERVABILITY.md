# TBD: Self-Hosted Observability

## Goal

Move from Grafana Cloud + Alloy shipping to a self-hosted observability stack on
the Hetzner server.

This is not urgent for the Hetzner backend cutover. First restore visibility by
installing Alloy on Hetzner and keeping the current Grafana Cloud destination.
Then decide if self-hosting is worth the operational cost.

## Candidate Stack

```text
Grafana      dashboards and UI
Prometheus   metrics database
Loki         logs database
Promtail or Alloy  collector
Nginx        reverse proxy
```

Optional later:

```text
Alertmanager
Tempo
Grafana backups
S3-compatible object storage
```

## Key Decisions To Make Later

- Public access:
  - private VPN only;
  - HTTP basic auth behind Nginx;
  - real domain + HTTPS + Grafana auth.
- Retention:
  - logs: 7, 14, or 30 days;
  - metrics: 15, 30, or 90 days.
- Storage:
  - local NVMe only for MVP;
  - external backup/object storage later.
- Collection:
  - keep Alloy as the one collector;
  - or use Promtail for Loki + node exporter/cAdvisor for Prometheus.
- Deployment:
  - Docker Compose first;
  - no Kubernetes until multiple servers exist.

## Why Not Now

Self-hosted observability adds:

- exposed admin UI;
- persistent volumes;
- retention tuning;
- backups;
- security/auth;
- more services to monitor.

The current immediate need is simpler: get Hetzner backend logs/metrics visible
in the existing Grafana Cloud workspace.

## Future Implementation Sketch

- Add `deploy/observability/docker-compose.yml`.
- Add Nginx config for Grafana.
- Add persistent volumes for Grafana, Prometheus, and Loki.
- Add retention limits before first production use.
- Move dashboards from Grafana Cloud into repo-backed JSON.
- Disable old Grafana Cloud shipping only after self-host dashboards show:
  - backend logs;
  - host CPU/RAM/disk;
  - container CPU/RAM;
  - backend health.

## Assumptions

- Hetzner server has enough RAM and NVMe storage for MVP observability.
- Supabase remains managed and is not part of this migration.
- Self-hosting is a cost/control decision, not required for backend uptime.
