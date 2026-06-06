# TASKS 31: Self-Hosted Observability On Hetzner

Status: In Progress. Local compose/config/provisioning files are implemented
and the backend monorepo deploy workflow now ships stack files. Nginx/certbot
setup, Alloy dual-write, and Cloud cutover are still pending.

## Goal

Move production observability off **Grafana Cloud** onto a **self-hosted stack on
the same Hetzner host** that already runs the backend. Self-host Grafana, Loki
(logs), and Prometheus (metrics). Keep the existing Alloy agent as the single
collector; only switch its destinations from Grafana Cloud to local endpoints.

This supersedes the thinking doc `TBD_SELF_HOST_OBSERVABILITY.md`.

Reuse what already exists:

- the `grafana` service already defined in `../../docker-compose.yml` behind the
  `observability` profile;
- the three dashboard JSON files already in `grafana/dashboards/`;
- the Alloy host pipelines from `TASKS_5_LOGGING.md` (logs) and
  `TASKS_10_SERVER_METRICS.md` (host + container metrics).

## Decisions (fixed for MVP, tune later)

- **Stack**: Grafana + Loki + Prometheus, single-binary each, Docker Compose under
  the existing `observability` profile. No Kubernetes.
- **Host**: same Hetzner prod box (`/opt/backend_sloco`). Shares resources with the
  app — check RAM/NVMe headroom before cutover.
- **Access**: subdomain `grafana.sloco.pp.ua` over HTTPS via the existing
  Nginx + certbot setup (same pattern as `TASKS_20_DOMAIN_HTTPS_NGINX_HARDENING.md`),
  with Grafana's own login. No VPN.
- **Retention**: logs **14 days** (Loki), metrics **30 days** (Prometheus).
- **Storage**: local NVMe, Docker named volumes. Object-storage/backups are later.
- **Collector**: keep **Alloy** as the only collector. Do not add Promtail /
  node_exporter / cAdvisor containers — Alloy's built-in exporters stay as-is.
- **Provisioning**: datasources and dashboards are provisioned as code. No more
  manual import flow.
- **Cutover**: stand up local stack → Alloy dual-writes (Cloud + local) → verify
  local dashboards → remove Cloud destinations and revoke Cloud tokens.

## Target Architecture

```text
Hetzner host (/opt/backend_sloco)
  Alloy (systemd on host, unchanged pipelines, new destinations)
    ├─ loki.write             → http://127.0.0.1:3100/loki/api/v1/push
    └─ prometheus.remote_write → http://127.0.0.1:9090/api/v1/write

  docker compose --profile observability:
    loki        127.0.0.1:3100  logs DB,   retention 14d, volume loki_data
    prometheus  127.0.0.1:9090  metrics DB, retention 30d, volume prometheus_data,
                                --web.enable-remote-write-receiver
    grafana     127.0.0.1:3001  provisioned datasources + dashboards, volume grafana_data

  Nginx: grafana.sloco.pp.ua (HTTPS) → 127.0.0.1:3001
```

Note on ports: Loki and Prometheus are bound to `127.0.0.1` on the host (not
`expose`-only) because **Alloy runs on the host**, not in the compose network, and
must reach them over loopback. Grafana reaches them inside `sloco_net` by service
name (`http://loki:3100`, `http://prometheus:9090`).

## Files In This Change

```text
backend/docker-compose.yml                                      (edit: add loki, prometheus; extend grafana)
backend/deploy/observability/loki-config.yml                    (new)
backend/deploy/observability/prometheus.yml                     (new)
backend/deploy/nginx/grafana_sloco.conf                         (new)
backend/services/gateway/grafana/provisioning/datasources/datasources.yml  (new)
backend/services/gateway/grafana/provisioning/dashboards/dashboards.yml     (new)
backend/services/gateway/grafana/dashboards/*.json               (edit: fix datasource refs for provisioning)
backend/services/gateway/grafana/README.md                       (edit: provisioning replaces manual import)
backend/.env.example                                            (edit: GF_SERVER_ROOT_URL, keep admin creds)
```

## Step 1 — Docker Compose

Add to `backend/docker-compose.yml` under the `observability` profile and extend the
existing `grafana` service. Match the existing service conventions
(`restart: unless-stopped`, `networks: [sloco_net]`, json-file logging 10m×3).

```yaml
  loki:
    image: grafana/loki:3.1.1
    profiles: [observability]
    restart: unless-stopped
    command: ["-config.file=/etc/loki/config.yml"]
    volumes:
      - ./deploy/observability/loki-config.yml:/etc/loki/config.yml:ro
      - loki_data:/loki
    ports:
      - "127.0.0.1:3100:3100"   # host-loopback so on-host Alloy can push
    networks: [sloco_net]
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  prometheus:
    image: prom/prometheus:v2.54.1
    profiles: [observability]
    restart: unless-stopped
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=30d"
      - "--web.enable-remote-write-receiver"
    volumes:
      - ./deploy/observability/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    ports:
      - "127.0.0.1:9090:9090"   # host-loopback so on-host Alloy can remote_write
    networks: [sloco_net]
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

Extend the existing `grafana` service (keep `profiles`, port, `grafana_data`, and
the read-only dashboards mount) with provisioning mounts, datasource ordering, and
a root URL so links behind Nginx are correct:

```yaml
  grafana:
    image: grafana/grafana:latest
    profiles: [observability]
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_USER: ${GF_SECURITY_ADMIN_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GF_SECURITY_ADMIN_PASSWORD:-change-me}
      GF_SERVER_ROOT_URL: ${GF_SERVER_ROOT_URL:-http://localhost:3001}
      GF_USERS_ALLOW_SIGN_UP: "false"
    ports:
      - "127.0.0.1:3001:3000"
    depends_on:
      - loki
      - prometheus
    volumes:
      - grafana_data:/var/lib/grafana
      - ./services/gateway/grafana/dashboards:/var/lib/grafana/dashboards:ro
      - ./services/gateway/grafana/provisioning:/etc/grafana/provisioning:ro
    networks: [sloco_net]
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }
```

Add the new named volumes at the bottom of the file:

```yaml
volumes:
  redis_data:
  grafana_data:
  loki_data:
  prometheus_data:
```

`make up-all` already runs `--profile cache --profile observability`, so the whole
stack comes up with the existing target.

## Step 2 — Loki Config

`backend/deploy/observability/loki-config.yml` — single binary, filesystem storage,
14-day retention.

```yaml
auth_enabled: false

server:
  http_listen_port: 3100

common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

limits_config:
  retention_period: 336h          # 14 days
  reject_old_samples: true
  reject_old_samples_max_age: 168h
  volume_enabled: true

compactor:
  working_directory: /loki/compactor
  delete_request_store: filesystem
  retention_enabled: true
```

## Step 3 — Prometheus Config

`backend/deploy/observability/prometheus.yml` — Prometheus is mainly a
remote-write **receiver** for Alloy; no app scrape targets needed for MVP. Retention
is set via the CLI flag in compose, not here.

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:9090"]   # self-scrape only; Alloy pushes the rest
```

## Step 4 — Grafana Provisioning (datasources + dashboards as code)

`backend/services/gateway/grafana/provisioning/datasources/datasources.yml`:

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
    jsonData:
      maxLines: 1000
```

`backend/services/gateway/grafana/provisioning/dashboards/dashboards.yml`:

```yaml
apiVersion: 1
providers:
  - name: sloco
    orgId: 1
    folder: Sloco
    type: file
    disableDeletion: false
    allowUiUpdates: true
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
```

## Step 5 — Dashboards (keep the existing three, fix datasource refs)

We already have three working dashboards. Keep them; they cover the right ground.
Do **not** rebuild from scratch for the cutover — just make them provisioning-ready.

```text
grafana/dashboards/backend-logs.json     (Loki)        live logs, req/resp, map, errors, slow
grafana/dashboards/backend-metrics.json  (Loki)        HTTP/Supabase latency, P95, slow calls
grafana/dashboards/server-metrics.json   (Prometheus)  host CPU/RAM/disk/net, container CPU/RAM/restarts
```

The JSON currently uses import variables (`${DS_LOKI}`, `${DS_PROM}`) that prompt at
manual import. For provisioning, make each file load against the fixed datasource
UIDs from Step 4:

- remove the top-level `__inputs` and `__requires` blocks;
- replace every `"datasource": {"type": "loki", "uid": "${DS_LOKI}"}` →
  `"uid": "loki"`;
- replace every `"datasource": {"type": "prometheus", "uid": "${DS_PROM}"}` →
  `"uid": "prometheus"`;
- (`backend-metrics.json` is Loki-based — it uses `DS_LOKI`, not Prometheus.)

After this, the file provider loads all three into the `Sloco` folder automatically
on Grafana start — no UI import.

> Optional follow-up (not blocking cutover): the dashboards are functional but
> dense. A later rebuild could split into clearer folders (App / Host / Logs),
> add an overview home dashboard, and move the Loki-derived app metrics
> (`backend-metrics.json`) to real Prometheus histograms once the backend exposes a
> `/metrics` endpoint (see `prom-client` follow-up in `TASKS_10`). Track separately.

## Step 6 — Nginx + HTTPS For Grafana

`backend/deploy/nginx/grafana_sloco.conf` — vhost mirroring
`deploy/nginx/backend_sloco.conf`, proxying the subdomain to Grafana:

```nginx
server {
    listen 80;
    server_name grafana.sloco.pp.ua;
    location / { proxy_pass http://127.0.0.1:3001; }
}
```

On the host:

```bash
# DNS: point grafana.sloco.pp.ua A-record at the host first.
sudo cp /tmp/grafana_sloco.conf /etc/nginx/sites-available/grafana_sloco.conf
sudo ln -s /etc/nginx/sites-available/grafana_sloco.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d grafana.sloco.pp.ua    # issues cert + rewrites vhost to 443
```

Set `GF_SERVER_ROOT_URL=https://grafana.sloco.pp.ua` in `/opt/backend_sloco/.env`
so generated links and OAuth/redirects are correct. Add WebSocket/headers if Grafana
Live is needed:

```nginx
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
```

## Step 7 — Server Setup / Cutover Runbook

All commands run on the Hetzner host in `/opt/backend_sloco` unless noted.

### 7.1 Ship the new files through CI/CD

The backend monorepo deploy workflow now copies compose/config/dashboard files to
the host and renders `/opt/backend_sloco/.env` from GitHub secrets. Run:

```text
.github/workflows/deploy-production.yml
```

Use `service=all` for the first observability deploy so both app services and
the full stack converge together.

Set these GitHub production secrets before the run:

```bash
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=<strong-password>
GF_SERVER_ROOT_URL=https://grafana.sloco.pp.ua
```

### 7.2 Validate and start the stack

```bash
cd /opt/backend_sloco
docker compose --profile observability config
docker compose ps
```

### 7.3 Nginx + HTTPS

Do Step 6 (vhost + certbot). Open `https://grafana.sloco.pp.ua`, log in, confirm the
`Sloco` folder has all three dashboards and both datasources exist under
Connections → Data sources.

### 7.4 Point Alloy at the local stack (dual-write first)

Alloy stays a host systemd service (`/etc/alloy/config.alloy`, not versioned). Keep
the existing exporter pipelines from `TASKS_5_LOGGING.md` / `TASKS_10`. Change only
the **destinations** — first add local **alongside** Cloud, so nothing goes dark:

```alloy
// Local logs sink (dual-write with existing Grafana Cloud loki.write)
loki.write "local" {
  endpoint { url = "http://127.0.0.1:3100/loki/api/v1/push" }
  external_labels = { env = "production", host = constants.hostname }
}

// Local metrics sink (dual-write with existing remote_write to Grafana Cloud)
prometheus.remote_write "local" {
  endpoint { url = "http://127.0.0.1:9090/api/v1/write" }
  external_labels = { env = "production", host = constants.hostname }
}
```

Add `loki.write.local.receiver` and `prometheus.remote_write.local.receiver` to the
`forward_to` lists of the existing `loki.source.*` and `prometheus.scrape.*` blocks
(so they fan out to both Cloud and local). Then:

```bash
alloy fmt /etc/alloy/config.alloy
sudo systemctl restart alloy
sudo systemctl status alloy
sudo journalctl -u alloy -n 100 --no-pager      # no push/remote_write errors
```

### 7.5 Verify, then cut over

After local dashboards show real data (see Test Plan), remove the Grafana Cloud
`loki.write` / `prometheus.remote_write` destinations from `forward_to`, restart
Alloy, and revoke the Grafana Cloud access tokens in Grafana Cloud → Access Policies.
Stop using the Cloud stack.

## Test Plan

Local (workstation, before touching the server):

```bash
cd backend
docker compose --profile observability config
docker compose --profile observability up -d
# dashboards parse:
for f in services/gateway/grafana/dashboards/*.json; do \
  node -e "JSON.parse(require('fs').readFileSync('$f','utf8')); console.log('ok $f')"; done
```

- Grafana `http://127.0.0.1:3001`: both datasources present (provisioned), all three
  dashboards appear in the `Sloco` folder without manual import.

On the server after dual-write:

- Explore → Loki: `{service="backend"}` returns fresh logs.
- Explore → Prometheus: `node_load1`, `node_memory_MemAvailable_bytes`, and
  `container_cpu_usage_seconds_total{name=~".*backend.*"}` return data.
- Generate traffic and confirm panels populate + a visible CPU/network spike:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

- Alloy: `alloy fmt` clean, `systemctl status alloy` active, journal has no
  remote_write/loki push errors.
- Persistence: `docker compose restart loki prometheus grafana` keeps data; logs
  older than 14d / metrics older than 30d are dropped.

## Security

- Set a strong `GF_SECURITY_ADMIN_PASSWORD`; never commit it. `change-me` is only a
  local default.
- Grafana is reachable only via Nginx/HTTPS on the subdomain; the container port
  stays bound to `127.0.0.1:3001`. Loki/Prometheus stay on `127.0.0.1` (host-local) —
  not exposed publicly.
- Do not commit Grafana Cloud tokens, Loki push URLs, or Prometheus instance IDs.
  Revoke Cloud tokens after cutover.
- `auth_enabled: false` on Loki is safe only because it is loopback-bound and behind
  the Docker network; do not publish 3100/9090 to `0.0.0.0`.

## Assumptions

- Hetzner host has spare RAM/NVMe to run Loki + Prometheus + Grafana next to the app.
- `grafana.sloco.pp.ua` DNS can be created and a Let's Encrypt cert issued.
- Existing Alloy exporter pipelines (logs + host/container metrics) keep working;
  only destinations change.
- Manual file copy for compose/config is acceptable for this infra step; automating
  it belongs with the CI/CD redesign in `../../docs/tasks/TBD_CICD_SECRETS_AND_RUNNERS.md`.

## Follow-Ups

- Alertmanager + alert rules (CPU/RAM/disk thresholds, 5xx spikes).
- Backend `/metrics` endpoint via `prom-client`; move app latency off Loki-derived
  metrics to real Prometheus histograms; rebuild `backend-metrics.json` accordingly.
- Supabase Metrics API scrape into the same Prometheus (DB CPU/RAM, connections,
  pooler signals) — from `TBD_SELF_HOST_OBSERVABILITY.md`.
- Object-storage backend + backups for Loki/Prometheus once retention needs grow.
- Dashboard rebuild into App / Host / Logs folders with an overview home.
