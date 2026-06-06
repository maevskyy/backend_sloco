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

- **Target**: reuse the existing gateway repo **`backend_sloco`** as the monorepo
  (name kept, not renamed); iOS (`frontend_sloco`) stays separate.
- **git history**: keep the gateway repo's history — its files move into the
  `gateway_service/` subfolder with rename detection, so gateway blame is preserved.
  `recommendation_service` and the old infra repo histories are NOT carried over; they
  remain in their archived repos.
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

## Step 1 — Repo Consolidation (DONE)

The gateway repo `backend_sloco` became the monorepo by promoting its `.git` to the
`backend/` root, so the gateway tree moved into `gateway_service/` with full rename
detection (191 renames at R100 → blame preserved). Executed:

```bash
# from the project root
rm -rf backend/recommendation_service/.git   # drop nested service git
rm -rf backend/.git                          # drop infra git (sloco_backend_infra)
mv backend/gateway_service/.git backend/.git # gateway git becomes the monorepo git
mv algorythms backend/algorithms             # bring algorithms under version control

# fix root .gitignore: it ignored gateway_service/ and recommendation_service/ —
# removed those, added node/python junk ignores (node_modules, __pycache__, .venv, ...)

cd backend
git add -A
git commit   # "chore: consolidate backend into monorepo"
git rm -r gateway_service/.github recommendation_service/.github
git commit   # "chore: remove superseded per-service workflows"
git push origin main           # remote stays backend_sloco.git
```

Then on GitHub (via `gh repo archive`):
`sloco_backend_infra` and `backend_sloco_recommendation_service` → **archived**
(read-only). `backend_sloco` kept as the monorepo; `frontend_sloco` untouched.

Notes:
- GHCR image names stay (`ghcr.io/maevskyy/backend_sloco`, `.../recommender_sloco`)
  so prod `.env` tags keep working.
- **GHCR package permissions to verify on first deploy**: the `recommender_sloco`
  package was linked to the now-archived recommendation repo. The monorepo's
  `GITHUB_TOKEN` may lack write access to it. If the deploy push fails, either grant
  the `backend_sloco` repo write access in the package settings, or repoint the
  recommender image under the monorepo's package namespace.

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
