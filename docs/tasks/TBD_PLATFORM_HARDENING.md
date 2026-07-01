# TBD: Platform Hardening — Known Gaps & Concerns

## Why This Exists

After the monorepo consolidation, automated deploy, and self-hosted observability
landed, the platform is in good shape operationally. This document records the
**known gaps** that keep it from being production-hardened, so they are not lost.

It is a thinking/backlog document, not an implementation task. Each concern has a
direction, not a committed plan. Items already owned by other docs are linked, not
duplicated.

## Snapshot (for context)

Honest self-assessment after the monorepo deploy and self-hosted observability
were tested on production: **~8.7 / 10** for a solo MVP backend platform.

| Axis | Score | Note |
| --- | --- | --- |
| Code architecture (gateway/recommender) | 8.5 | layered modules, Zod→OpenAPI, clean boundaries |
| Service boundaries (polyglot split) | 8.5 | public Node gateway + private Python recommender |
| Repo topology | 9 | monorepo, `services/`, code vs infra split, no nested repos |
| Deploy / CI-CD | 8.7 | prod-tested, verify-gated, stack-owning, observability opt-in |
| Observability | 8.5 | self-hosted Grafana/Loki/Prometheus live; no alerts/backups yet |
| Secrets / security | 7.5 | one contour; no rate-limit; SOPS not done |
| **Testing / load** | 5 | harness exists, never run; no measured SLO |
| Data / algorithm | 6 | simplified recommender port; no embedding pipeline |
| Resilience / DR | 6 | stateless host, but single box, no staging, no backups |

The remaining points are not "rewrite" — they are **prove and protect**.

## Done

- Backend is now one monorepo with `services/gateway`, `services/recommendation`,
  root `deploy/`, root `docker-compose.yml`, root CI/CD, and one secret contour.
- Production deploy was tested through `.github/workflows/deploy-production.yml`.
- Stack files are shipped by CI/CD; normal deploys no longer require manual `scp`.
- Redis is a normal runtime dependency and works for `GET /v1/places/:id` cache.
- Self-hosted Grafana/Loki/Prometheus are deployed/provisioned and usable.
- Grafana dashboards are provisioned from repo files instead of manual import.
- Load-test harness exists under `load/`.

## Not Done Yet

- Real load baseline and measured SLOs.
- Alertmanager / alert rules.
- Staging environment.
- Rollback drill.
- Backups/retention validation for Grafana/Loki/Prometheus volumes.
- Reproducible embedding-generation pipeline.
- Rate limiting / additional app security hardening.
- Final Grafana Cloud token/account deletion if it has not already been completed.

## Concerns (prioritized)

### 1. Load is unmeasured — no real SLOs  (HIGHEST)

The Artillery harness (`load/`) exists but has **never been run** against staging or
prod. The `/v1/map/places` hot path — the most important and complex read path — has
**no measured latency**. The recommendation service has **zero load tests** and no
latency SLO. SLO numbers in `load/README.md` are placeholders.

- Why it matters: "seems fast" is not "holds p95 < X ms at N rps." Map is the path
  most likely to fall over and the one we most want to be sure about.
- Direction: run the harness against a staging box (not prod by default), record a
  real baseline, set true SLOs, find the breaking RPS, watch host/container CPU on
  the Grafana dashboards during the run.

### 2. No alerting

Self-hosted Grafana/Prometheus/Loki are live, but there is **no Alertmanager and no
alert rules**. Data is visible but nothing pages on CPU/RAM/disk pressure, 5xx
spikes, container restarts, or the backend going down.

- Direction: Alertmanager + a small set of rules (host CPU/RAM/disk thresholds, 5xx
  rate, container up/down, disk-free for observability volumes). Route to a cheap
  channel (Telegram/email).

### 3. No staging environment

Deploy goes straight to prod, on the **same single box** that now also runs the
observability stack. There is nowhere safe to test the deploy, the observability
cutover, or load tests. Under load, monitoring competes with the app for resources.

- Direction: a small staging target (separate box or at least a separate compose
  project/profile) — also unblocks safe load testing (concern 1).

### 4. Observability durability / single box

Loki and Prometheus data live in Docker named volumes on local NVMe of one Hetzner
box. No backups; retention (logs 14d / metrics 30d) is set but **untested under real
volume**. If the box dies, observability history is lost (app data is safe in
Supabase). The same box runs app + monitoring — a single point of failure.

- Direction: decide retention vs disk budget after real volume is known; consider
  object-storage backend or periodic volume backup; longer term, move observability
  off the prod box.

### 5. Algorithm drift + no embedding pipeline

Prod defaults to `embedding_recommender_v1` — a **simplified** numpy-only port. The
data team's full engine is now vendored as `location_recommender_v4`
(`services/recommendation/src/recommendation_service/algorithms/location_recommender/`)
and selectable via `RECOMMENDER_ALGORITHM` (see
`services/recommendation/docs/TASKS_2_location_recommender_v4.md`). Remaining to
enable it: the data cutover (reimport `places` in the new `cid`-keyed format via a
new `sloco` mapper) and flipping the prod flag. The embedding `.npy` artifacts still
have **no reproducible generation flow** — no pipeline to regenerate or refresh them.

- Direction: finish the data cutover + enable v4 (staging → prod); build a
  reproducible embedding-generation job (the notebook is in git history). Tracked in
  `TASKS_2_location_recommender_v4.md`.

### 6. Secrets / app security

Secrets are now in one contour (GitHub repo secrets) — good — but:
- no centralized manager / SOPS encrypted-in-repo yet (optional hardening);
- the gateway has **no rate limiting** (no per-IP/user budget);
- `SUPABASE_SERVICE_ROLE_KEY` is full DB access bypassing RLS — handle with care
  (never in chat/logs; rotate if exposed).
- Broader CI/CD + secrets redesign is owned by
  `TBD_CICD_SECRETS_AND_RUNNERS.md`.

### 7. Map calibration is city-specific

Map visibility/ranking thresholds in the gateway are calibrated for **Bucharest**
only (zoom 10–16 ranges). A second city will need retuning. The feed recommendation
cache is **in-memory** (500 entries, 10 min) and will leak under many users.

- Direction: make thresholds city-parametric or density-derived; move the feed cache
  to Redis when load grows. Tracked in `TASKS_2` follow-ups.

### 8. GHCR package linkage fragility

The `recommender_sloco` GHCR package was linked to the now-archived recommendation
repo, which broke the first deploy (`permission_denied: write_package`) until
`backend_sloco` was granted Write on the package. Worth tidying so future package
permissions are obvious (e.g. confirm both images' Actions-access is the monorepo).

## Priority Order

1. Run load tests + set real SLOs (concern 1).
2. Alerting (concern 2).
3. Staging environment (concern 3) — also unblocks 1.
4. Observability durability/backups (concern 4).
5. Algorithm pipeline (concern 5).
6. Rate limiting + secrets hardening (concern 6).
7. City-parametric map calibration (concern 7).
8. GHCR package tidy-up (concern 8).

## Non-Goals For Now

Do not over-engineer the MVP:
- no Kubernetes, service mesh, multi-region;
- no Kafka/RabbitMQ;
- no full secret manager (SOPS optional, not required);
- no separate observability cluster — one box is fine until load says otherwise.

## Related Docs

- `TASKS_2_BACKEND_MONOREPO_CONSOLIDATION.md` — consolidation + deploy + load harness
  (its follow-ups overlap with concerns 5 and 7).
- `TBD_CICD_SECRETS_AND_RUNNERS.md` — CI/CD + secrets redesign (concern 6).
- `services/gateway/docs/tasks/TBD_SELF_HOST_OBSERVABILITY.md` — superseded; the
  self-host stack is now live, alerting/backups remain (concerns 2, 4).
- `services/gateway/docs/tasks/TBD_TRACING_LATENCY_BREAKDOWN.md` — future OpenTelemetry
  tracing (related to concern 1's latency visibility).
