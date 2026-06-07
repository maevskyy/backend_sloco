# TASKS 3: Observability As A Top-Level Concern

**Status: In Progress.**

## Context

After the monorepo consolidation, observability config was split and inconsistent:

- the Grafana service and Loki/Prometheus configs are platform-level (root
  `docker-compose.yml` + `deploy/observability/`), **but** the Grafana dashboards,
  provisioning, and datasources lived inside one service: `services/gateway/grafana/`;
- `server-metrics.json` (host/container metrics) sat under `gateway`, which is
  semantically wrong — it is not gateway-specific;
- compose mounted the Grafana part from a service folder while pulling Loki/Prometheus
  from `deploy/` — two different trees for one concern.

Observability is its own concern (its own lifecycle, and alerting is coming). This
task moves **all** observability config into a single top-level `observability/`
directory, with dashboards organized by **subfolder** (`app/`, `infra/`) instead of
per-service or scattered.

## Decisions

- New top-level **`observability/`** owns: Grafana provisioning + dashboards,
  Prometheus config + (future) alert rules, Loki config, (future) Alertmanager.
- Dashboards grouped by area subfolder; the Grafana file provider uses
  `foldersFromFilesStructure: true` so subfolders become Grafana folders (no
  per-service dashboard dirs, no "30 folders").
- Monitoring **services stay in the root `docker-compose.yml` under the
  `observability` profile** for now. A separate `observability/docker-compose.yml`
  (via compose `include:`) is deferred to when monitoring moves to its own box
  (`TBD_PLATFORM_HARDENING.md`, concerns 3/4). YAGNI until then.
- Nginx vhosts (`deploy/nginx/grafana_sloco.conf`) **stay in `deploy/nginx/`** — the
  public edge is a separate concern from the monitoring stack config.
- Not a new repo — `observability/` is a folder in the monorepo.

## Target Layout

```text
observability/
  grafana/
    provisioning/
      datasources/datasources.yml      Loki + Prometheus (platform)
      dashboards/dashboards.yml          file provider, foldersFromFilesStructure
    dashboards/
      app/                               application dashboards
        backend-logs.json
        backend-metrics.json
      infra/                             host/container dashboards
        server-metrics.json
  prometheus/
    prometheus.yml
    rules/                               (placeholder) alert/recording rules
  loki/
    loki-config.yml
  README.md
```

Removed (become empty): `services/gateway/grafana/`, `deploy/observability/`.

## Changes

1. **Move** (git mv) dashboards into `app/`+`infra/`, provisioning, README, and the
   Loki/Prometheus configs into `observability/`. Add `prometheus/rules/.gitkeep`.
2. **Provider** `observability/grafana/provisioning/dashboards/dashboards.yml`: add
   `options.foldersFromFilesStructure: true`, keep `options.path:
   /var/lib/grafana/dashboards`, drop the static `folder: Sloco`.
3. **`docker-compose.yml`** mounts:
   - grafana → `./observability/grafana/dashboards`, `./observability/grafana/provisioning`;
   - loki → `./observability/loki/loki-config.yml`;
   - prometheus → `./observability/prometheus/prometheus.yml`.
4. **`.github/workflows/deploy-production.yml`** scp `source` → sync
   `docker-compose.yml,deploy/**,observability/**`.
5. **`.github/workflows/ci.yml`** dashboards job → path filter
   `observability/grafana/dashboards/**` + recursive JSON validation (dashboards are
   now nested under `app/`/`infra/`).
6. **Docs**: `docs/ARCHITECTURE.md` (layout, grafana row, what-belongs-where),
   `services/gateway/AGENTS.md` (remove `grafana/` from this service — it moved out),
   `docs/README.md`, and a note in `TASKS_2`.

## Test Plan

- `docker compose --profile observability config -q` valid (mounts resolve under
  `observability/`).
- YAML parse: `ci.yml`, `deploy-production.yml`, `dashboards.yml`, `datasources.yml`.
- Recursive glob finds all 3 dashboards; each parses as JSON.
- `grep -rn 'services/gateway/grafana\|deploy/observability'` over compose/.github/docs
  is empty.
- Optional local: `docker compose --profile observability up -d grafana` → Grafana UI
  shows `app` and `infra` folders with the dashboards.
- Server runtime is validated on the next `with_observability` deploy (scp
  `observability/**` + restart). `/opt/backend_sloco` is not broken meanwhile — old
  paths simply stop being used.

## Notes

- Pure config/structure move; no service code changes.
- Sets up the home for alerting (concern 2 in `TBD_PLATFORM_HARDENING.md`):
  `observability/prometheus/rules/` and a future `observability/alertmanager/`.
