# TASKS 2: Backend Monorepo Consolidation + Deploy/Secrets/Load

**Status: In Progress.**

## Context

The backend code architecture is healthy, but the **operational topology** is not.
Three backend GitHub repos (`sloco_backend_infra`, `backend_sloco`,
`backend_sloco_recommendation_service`) split a single deploy unit across
repositories:

- the unified `docker-compose.yml` lives in `sloco_backend_infra` but references
  `./gateway_service` and `./recommendation_service`, which are *separate* repos
  checked out as nested folders;
- the compose file is copied to the server by hand (`scp`), no pipeline owns it;
- the same 6 secrets (`DEPLOY_HOST/USER/SSH_KEY`, `GHCR_USERNAME/READ_TOKEN`,
  `PRODUCTION_API_URL`) are configured once per service repo — N× duplication as
  services grow;
- `algorythms/` is outside version control;
- `sloco_backend_infra` has no CI — it is a passive template holder.

This task collapses the three backend repos into **one monorepo** with flat
subfolders (NOT git submodules), keeping the runtime service split (Node gateway +
Python recommender — a correct polyglot boundary). It also lands proper deployment,
a single secret contour, and a load-testing harness.

This closes `TBD_CICD_SECRETS_AND_RUNNERS.md`.

**This is not a code rewrite.** Service-internal architecture is untouched. Only repo
topology, delivery, and automation change.

## Decisions

- **Target**: one repo `sloco_backend`; iOS (`frontend_sloco`) stays separate.
- **git history**: start fresh (one import commit); old repos archived read-only for
  blame/reference.
- **No submodules** — plain subfolders, one history.
- **No** Kafka/RabbitMQ, Kubernetes, service mesh, Kong/Tyk, or Terraform now.

## Target Layout

```text
sloco_backend/
  docker-compose.yml            root, owns the whole stack
  docker-compose.override.yml
  Makefile                      adds `load` target
  .env.example
  deploy/
    nginx/                      backend_sloco.conf + grafana_sloco.conf
    observability/              loki/prometheus configs (TASKS_31)
  gateway_service/              Node/Fastify (unchanged code; nested .git removed)
  recommendation_service/       Python/FastAPI (unchanged code; nested .git removed)
  algorithms/                   former algorythms/ — now versioned
  load/                         Artillery scenarios + README (SLOs)
  docs/                         merged docs, DECISIONS, tasks
  .github/workflows/            ci.yml (path-filtered) + deploy-production.yml
```

New services (`stats_service`, future algo services) = a new subfolder. Zero new
repos, zero new secret setup.

## Step 1 — Repo Consolidation (git surgery; run with explicit go-ahead)

Irreversible and outward-facing. The on-disk `backend/` directory already has the
right shape, so consolidation is mostly removing nested `.git` dirs and bringing in
`algorythms/`.

```bash
# from the project root: /Users/.../tripadviser_zenly_nomadtable
# 0. SAFETY: back up first
cp -R backend backend.bak && cp -R algorythms algorythms.bak

# 1. drop the nested service repos' git history (they become plain folders)
rm -rf backend/gateway_service/.git
rm -rf backend/recommendation_service/.git

# 2. bring algorithms under the monorepo
git -C backend rm -r --cached --ignore-unmatch . >/dev/null 2>&1 || true
mv algorythms backend/algorithms

# 3. the backend/ repo (was sloco_backend_infra) becomes the monorepo.
#    Option A (keep infra history): just commit the now-merged tree.
#    Option B (start fresh, chosen): re-init for a clean single root.
cd backend
rm -rf .git
git init -b main
git add -A
git commit -m "chore: consolidate backend services into monorepo"

# 4. point origin at a fresh GitHub repo `sloco_backend` (create it on GitHub first)
git remote add origin https://github.com/maevskyy/sloco_backend.git
git push -u origin main
```

Then on GitHub, **archive** the old repos (read-only) for reference/blame:
`sloco_backend_infra`, `backend_sloco`, `backend_sloco_recommendation_service`.
Leave `frontend_sloco` alone.

Notes:
- GHCR image names can stay (`ghcr.io/maevskyy/backend_sloco`,
  `.../recommender_sloco`) — packages are account-scoped, not repo-bound, so existing
  prod `.env` tags keep working.
- Verify `.gitignore` covers `node_modules/`, `__pycache__/`, `dumps/` large files,
  and the embedding `.npy` artifacts if they should not be committed.

## Step 2 — Path-Filtered CI

One `.github/workflows/ci.yml`. A filter job decides which service changed; Node and
Python jobs run only when their folder changed (and always on workflow file change).
Each job sets `working-directory` to its subfolder.

- Node job (`gateway_service/**`): pnpm install/typecheck/build/test/lint.
- Python job (`recommendation_service/**`): poetry install/check-lock/ruff/mypy/pytest.
- Dashboards job (`gateway_service/grafana/**`): `node -e "JSON.parse(...)"` on each
  dashboard JSON.

Key change from today's per-repo CI: add `working-directory` + pnpm
`cache-dependency-path: gateway_service/pnpm-lock.yaml`, since the project is no
longer at repo root. See the committed `ci.yml` for the exact matrix.

## Step 3 — Deploy Owns The Whole Stack

One `deploy-production.yml` (`workflow_dispatch` with a `service` choice:
`gateway` / `recommender` / `all`). It removes the manual `scp` step:

1. build + push the selected service image(s) to GHCR;
2. `rsync` the root `docker-compose.yml`, `deploy/`, and
   `gateway_service/grafana/provisioning` to `/opt/backend_sloco`;
3. **render `/opt/backend_sloco/.env` from GitHub secrets** (heredoc over SSH), not
   hand-edited `sed`;
4. `docker compose --profile observability up -d` + health-check loop.

Result: adding Grafana/Loki/Prometheus (TASKS_31) becomes a normal push — no manual
server step. Re-running is idempotent.

## Step 4 — One Secret Contour

- One repo ⇒ one secret set. One SSH deploy key, one GHCR token, defined and rotated
  in a single place (repo secrets; promote to GitHub Environments `prod`/`staging`
  when a staging box appears).
- The deploy job is the only consumer; secrets never leave it except as the rendered
  server `.env`.
- Optional hardening: SOPS + age — commit an encrypted `.env` in-repo, decrypt with a
  single age key on deploy. Keeps "everything in one place" without external SaaS.

## Step 5 — Load Testing From Day One

`load/` with Artillery scenarios. Hot path first: `GET /v1/map/places` with varying
bbox + zoom, then `/v1/feed/places` and `/v1/places/:id`.

- Run locally: `make load` (target: a `BASE_URL`, default `http://127.0.0.1:3000`).
- SLOs recorded in `load/README.md`, e.g. p95 `/v1/map/places` < 150ms at a defined
  RPS. Today there is **no** load test and **no** measured latency for map or
  recommender — this closes that gap.
- Optional manual/nightly CI job against staging (never hammer prod by default).

## Test Plan / Verification

- `docker compose config` and `docker compose --profile observability config` valid
  from the monorepo root.
- `docker compose up -d` brings up gateway + recommender; `curl
  127.0.0.1:3000/v1/health` ok; gateway reaches recommender at
  `http://recommendation-service:8000/v1/health/ready`.
- CI: a change only under `gateway_service/**` triggers the Node job and skips the
  Python job (and vice versa); a `grafana/**` change triggers dashboard validation.
- Deploy: `workflow_dispatch` against a server with only `.env` secrets set brings up
  the full stack with no manual `scp`; second run is idempotent.
- Secrets: deploy key / GHCR token live in one place; rotating once applies to all
  services.
- Load: `make load` runs Artillery on `/v1/map/places` and prints p95/p99; SLOs in
  `load/README.md`.
- Old repos archived; `frontend_sloco` untouched.

## Assumptions / Risks

- Backing up `backend/` and `algorythms/` before git surgery is mandatory (Step 1.0).
- GHCR package names are account-scoped, so prod image tags survive the repo rename.
- The Hetzner deploy path `/opt/backend_sloco` stays the same; only delivery changes.

## Follow-Ups (separate tasks, after consolidation)

- Map "done right": city-parametric visibility thresholds (currently Bucharest-hard
  coded), Redis hot-viewport cache, hard load run.
- Recommender drift: prod `embedding_recommender.py` is a simplified port of
  `algorithms/.../backend_recommender.py` — decide to complete or freeze.
- Embedding pipeline: `.npy` artifacts have no reproducible generation flow.
- TASKS_31 self-host observability becomes trivial once Step 3 lands.
