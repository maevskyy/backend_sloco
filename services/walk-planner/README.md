# SLOCO Walk Planner (`walk-planner`)

The Walk Planner turns "I walk on Saturday from 10:00 to 14:00 and want a sight, a coffee, a park and lunch"
into 1–5 concrete walking routes through a city. Each route is a schedule: which real place to visit for each
requested activity, in which order, when you arrive, how long you stay, and when you finish. It checks opening
hours at the time of arrival, keeps the route inside the time window where it can, can add short stops on
the way, and personalises the choice of places by the user's favourite places. A planned route can be edited
(reorder, remove, change minutes, add a place) and is then re-timed. Walking legs come from a street router
(self-hosted OSRM, then the OpenRouteService cloud) or, when neither answers, from a straight-line estimate.
Every leg says which one it was.

This directory is one self-contained Python project. It holds the package `walk_planner` (algorithm, data
access, personalization, routing, API views, CLI) and the FastAPI microservice `walk-planner` built on it.
The service is internal and has no authentication. The app gateway (`backend_sloco`, Fastify/TS) calls it.
The research dashboard (Streamlit test bench) imports the same package, so the dashboard and the service
always plan the same way. City data arrives as immutable, versioned **data bundles** that are mounted
read-only. v1.0.0 is the dashboard's algorithm as of research-repo commit `f1b2196`, plus the bug fixes
and changes listed in [CHANGELOG.md](CHANGELOG.md). Recorded "golden" outputs pin its behaviour.

**Backend developer: start with [docs/HANDOFF.md](docs/HANDOFF.md).**

## Versions

| What | Value | Defined in |
|---|---|---|
| Algorithm / package / image tag | `1.0.0` | `walk_planner/version.py` `ALGORITHM_VERSION` |
| HTTP API | `v1` (every path starts with `/v1/`) | `API_VERSION` |
| Data bundle schema | `1` | `BUNDLE_SCHEMA_VERSION` |
| Interest (personalization) model | `walk_interest_v1` | `INTEREST_VERSION` |
| Data bundle, Bucharest | `bucharest-20261002-d68a311e` (12,961 places, 60.0 MB) | its `manifest.json`; expected outputs in `golden/expected/bucharest-20261002-d68a311e/` |
| Test bundle (synthetic "Minitown", committed) | `minitown-20261002-17bd5998` | `tests/fixtures/mini/` |
| OSRM image (street routing) | `ghcr.io/project-osrm/osrm-backend:v26.10.0-debian` | `deploy/docker-compose.yml`, `deploy/osrm/prepare_osrm.sh` |
| Python | `>=3.10`; the image runs 3.12.15 (`python:3.12.15-slim-trixie`) | `pyproject.toml`, `deploy/Dockerfile` |
| Release | git tag `walk-planner-v1.0.0` and its archives: **not cut yet** on 2026-10-03 | [docs/HANDOFF.md §1.1](docs/HANDOFF.md#11-release-identity) |

Every plan and edit response repeats the versions it was made with:
`versions = {api, algorithm, catalog (= bundle id), interest, routing (= OSRM dataset id or null)}`.
`GET /v1/meta` shows the running service's versions, git commit and bundles (its `versions` object has another
shape: [docs/API.md §3.3](docs/API.md#33-get-v1meta)).

## Repository layout

```text
services/walk_planner/
├── README.md, CHANGELOG.md
├── pyproject.toml          package "sloco-walk-planner"; extras: service, interest, dev; console script walk-planner
├── walk_planner/           the package
│   ├── core.py             route solver: per-slot beam search, on-the-way fill, timing and opening hours, navigation links
│   ├── slots.py            activity registry (codes, RU/EN labels, type filters), styles, shapes, tuning constants
│   ├── catalog.py          CityCatalog: one city's places (from a bundle or a DataFrame), hours, status, cards, photos, search
│   ├── candidates.py       per-slot and on-the-way candidate pools; must-visit / added-place status policy
│   ├── interest.py         place interest: cold-start popularity + taste model of the user's favourites (runtime)
│   ├── interest_build.py   builds the taste artifacts of a bundle (offline)
│   ├── pipeline.py         request validation and normalisation; plan, schedule (exact order), insert (best position)
│   ├── present.py          API JSON views; the dashboard's Russian text formatters
│   ├── messages.py         message and error catalog: codes, params, RU/EN texts, HTTP statuses
│   ├── routing.py          street routing chain OSRM -> ORS -> estimate, leg cache, breakers, rate limiter
│   ├── bundle.py           data bundle build / validate / load (manifest, sha256 of every file)
│   ├── cli.py, __main__.py developer CLI: python -m walk_planner <command>
│   ├── version.py          the four version identifiers above
│   └── service/            FastAPI app (app.py), pydantic schemas, environment settings, JSON logging
├── tests/                  pytest suite, offline; fixtures/mini/ = synthetic 60-place city with its bundle and golden set
├── golden/                 scenarios.json; expected/<bundle_id>/ (v1 API outputs); baseline_v0/ (pre-refactor); tools/
├── deploy/                 Dockerfile, entrypoint, docker-compose.yml, env.example, hash-pinned lock files,
│                           osrm/prepare_osrm.sh, sync_bundle.sh, photos.nginx.conf.example, uvicorn log config
├── tools/                  export_openapi.py, export_messages.py (write docs/openapi.json, docs/messages.json)
└── docs/                   the documents listed below
```

## Quickstart

All commands run from this directory (`services/walk_planner`).

### 1. Install

```bash
python3 -m venv .venv && . .venv/bin/activate

# Option A - any OS, Python >= 3.10: newest compatible dependency versions
pip install -e ".[service,dev]"

# Option B - exactly the production pins (what the image and CI use).
# Linux x86_64/aarch64 with CPython 3.12 only: the locks list Linux wheel hashes.
pip install --require-hashes --only-binary=:all: -r deploy/requirements.lock -r deploy/requirements-test.lock
```

With option B the package itself is not installed and does not need to be: the tests put this directory on
`sys.path`, and `python -m walk_planner` and `uvicorn ... --factory` find the package in the current
directory. The `service` extra pulls `uvicorn[standard]` (uvloop, httptools); the production lock pins plain
`uvicorn` with h11. Option A may resolve newer fastapi or pydantic versions than the locks; `tools/export_openapi.py
--check` ignores the version stamp, so that is harmless.

### 2. Run the tests

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
```

The suite is offline. Tests that need the real Bucharest data skip when it is absent. In the research repo,
with the data present, the result on 2026-10-03 was `663 passed` in 46 s (macOS, Python 3.13). The real-data
tests look for the bundle only where the research repo keeps it
(`recommendation_system/ai_location_recommender/data/walk_bundles`, three levels above `tests/`), and no variable
points them elsewhere. In an archive or a vendored copy they always skip (631 passed, 32 skipped with option A;
the extra skip is the `openapi.json` check of step 6): test the real bundle with `golden run --bundle` instead.

### 3. Run the service on the committed mini bundle

```bash
python -m walk_planner serve --bundle tests/fixtures/mini/bundles --port 8000
# the same with plain uvicorn:
WALK_BUNDLE_DIR=tests/fixtures/mini/bundles uvicorn walk_planner.service.app:create_app --factory --port 8000
```

The service logs JSON lines. `/v1/health/ready` answers 503 `not_ready` until the bundle is loaded,
verified and warmed up. Measured start-up (`startup_ms` in `GET /v1/meta`): 1.1 s on the mini bundle and
1.9 s on the Bucharest bundle, after about 1 s of imports. Without routing variables (`WALK_ROUTER_URL`,
`ORS_API_KEY`) every leg is the straight-line estimate, and the response says so. The service reads only
environment variables, never a `.env` file. `ENVIRONMENT` defaults to `production`, so a local run reports
`production` in `/v1/meta` unless you set `ENVIRONMENT=development`.

`WALK_BUNDLE_DIR` may name one bundle directory, a comma-separated list, or a directory of bundles. For a
directory of bundles the service loads the newest bundle per city. Inside the research repo,
`python -m walk_planner serve` without `--bundle` uses
`recommendation_system/ai_location_recommender/data/walk_bundles` (local data, not in git).

### 4. Call it

```bash
curl -s http://127.0.0.1:8000/v1/health/ready
# {"status":"ready","bundles":["minitown-20261002-17bd5998"]}

curl -s -X POST 'http://127.0.0.1:8000/v1/walks/plan?lang=en' -H 'Content-Type: application/json' \
  -d '{"city": "Minitown", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00",
       "shape": "loop", "start": "city_center", "slots": ["sight", "coffee", "park", "food"]}' > plan.json
```

`plan.json` holds `status: "ok"` and one variant with 4 stops, back at 13:44, with
`summary.routing: "estimate"`. The tiny mini city yields only one distinct route, and the `fewer_variants`
message says so. The other top-level fields are `plan_id`, `versions`, the normalised `request` echo,
`personalization`, the request `messages` and the `places` cards.

An edit sends the echo and a variant's `sequence` back. This example (needs `jq`) re-times variant 1 in
reverse order:

```bash
jq '{request, sequence: (.variants[0].sequence | reverse)}' plan.json \
  | curl -s -X POST 'http://127.0.0.1:8000/v1/walks/schedule?lang=en' -H 'Content-Type: application/json' -d @-
```

### 5. The CLI

```bash
python -m walk_planner plan --bundle tests/fixtures/mini/bundles --date 2026-10-03 \
  --start 10:00 --end 14:00 --slots sight,coffee,park,food --pretty
```

`--pretty` prints the dashboard's Russian summary: metrics, notes, stop cards and the Google Maps link.
Without it the command prints the same JSON as `POST /v1/walks/plan`. Other commands: `schedule`, `insert`,
`search`, `place`, `config`, `interest`, `route`, `bundle build|validate|info`, `golden run|update|list` and
`serve`. [docs/CLI.md](docs/CLI.md) lists every command and option; `python -m walk_planner <command> --help`
prints them too. Like the service, the CLI routes through the street-routing chain configured in the environment
(`--routing auto`, the default); `--routing estimate` forces straight lines. Unlike the HTTP API, `plan` without
`--date` plans today in the city's time zone.

### 6. Golden outputs and other checks

```bash
# the 26 Bucharest scenarios (needs the real bundle; about 20 s on a laptop)
python -m walk_planner golden run --bundle <dir of bucharest-20261002-d68a311e, or a dir of bundles>
# the 8 mini scenarios, in-process
python -m walk_planner golden run --bundle tests/fixtures/mini/bundles \
    --scenarios tests/fixtures/mini/scenarios.json --expected-root tests/fixtures/mini/expected
# the same scenarios replayed over HTTP against the running service of step 3
python -m walk_planner golden run --url http://127.0.0.1:8000 \
    --scenarios tests/fixtures/mini/scenarios.json --expected-root tests/fixtures/mini/expected
# generated docs are current
python tools/export_openapi.py --check
python tools/export_messages.py --check
```

`golden run` exits 0 only when every recorded response matches: strings exactly, numbers within 1e-6.
See [golden/README.md](golden/README.md).

- **The bundle.** `--bundle` takes a bundle directory, a comma-separated list or a directory of bundles. Without
  it, `golden run` uses `$WALK_BUNDLE_DIR`, and inside the research repo its data directory. In any other copy
  of the package, without either, it stops with exit 2 ("no bundle: pass --bundle PATH or set
  WALK_BUNDLE_DIR"). The expected set is chosen by the bundle's id: `golden/expected/` holds only
  `bucharest-20261002-d68a311e`, so the older build `bucharest-20261002-49fc956c` reports
  `26 scenarios: 26 missing` (its set was retired, see golden/README.md, "Bundle rebuild").
- **`--check` of the generated docs** compares the documents, ignoring the fastapi/pydantic version stamp
  (`info.x-generated-with`); with other library versions it says "current (only the version stamp differs)".
  A real schema change still fails it.
- **CI.** The workflow `.github/workflows/walk-planner-ci.yml` of the research repo (a copy ships as
  `deploy/ci/walk-planner-ci.yml`; adjust its `paths` / `working-directory` when you vendor the package)
  runs the tests on Python 3.12 with both lock files. It also builds the image and checks that it refuses a
  corrupt bundle, then replays the mini golden calls against the running container.
- **Docker.** [docs/DEPLOY.md §16](docs/DEPLOY.md#16-local-trial-with-docker-mini-bundle) runs the image, the
  compose file and the acceptance replay on a laptop with the mini bundle.

## Documents

| Document | Read it for |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | **Start here** (backend developer): what is delivered, the deployment and integration checklist, open questions |
| [docs/SPEC.md](docs/SPEC.md) | Master specification: scope, architecture, request lifecycles, personalization, routing, editing model, performance, security, known limitations, roadmap, glossary |
| [docs/API.md](docs/API.md) | HTTP API: endpoints, request and response fields, errors, examples |
| [docs/openapi.json](docs/openapi.json) | Generated OpenAPI 3 schema (`tools/export_openapi.py`) |
| [docs/messages.md](docs/messages.md), [docs/messages.json](docs/messages.json) | Message and error codes with params and RU/EN texts (the JSON is generated by `tools/export_messages.py`) |
| [docs/ALGORITHM.md](docs/ALGORITHM.md) | How places are chosen, ordered and timed; personalization; tuning constants; known defects |
| [docs/DATA.md](docs/DATA.md) | Data bundle format and validation, catalog fields, opening hours, place ids and Supabase, photos, building bundles, adding a city |
| [docs/ROUTING.md](docs/ROUTING.md) | Street routing chain, OSRM dataset, ORS fallback, leg cache |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Gateway and app integration: routes, id mapping, favourites, timeouts and retries, screen-by-screen mapping, client state for editing |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Deployment runbook: image, compose, environment, first deploy, routine operations, alerts, troubleshooting, a local trial with Docker |
| [docs/RELEASE.md](docs/RELEASE.md) | Versioning rules and how new versions are released and accepted |
| [docs/CLI.md](docs/CLI.md) | Every command and option of `python -m walk_planner` |
| [CHANGELOG.md](CHANGELOG.md) | What changed, per version |
| [golden/README.md](golden/README.md) | Golden scenarios, comparison rules, acceptance with `golden run --url` |
| [tests/fixtures/mini/README.md](tests/fixtures/mini/README.md) | The synthetic test city and how to regenerate it |
| [deploy/env.example](deploy/env.example) | The template of `walk.env`: its variables, with defaults and measured memory figures. The full list, including `WALK_ENV_FILE`, which the template lacks, is [docs/DEPLOY.md §5](docs/DEPLOY.md#5-environment-variables) |

Product background (research repo, `recommendation_system/ai_location_recommender/`):
[WALK_PLANNER_UX_BRIEF.md](../../recommendation_system/ai_location_recommender/WALK_PLANNER_UX_BRIEF.md) is the
brief the app screens were designed from.
[WALK_PLANNER_UX_TEST_REPORT.md](../../recommendation_system/ai_location_recommender/WALK_PLANNER_UX_TEST_REPORT.md)
holds the user-test findings that drive the roadmap.
[WALK_PLANNER_DESIGN.md](../../recommendation_system/ai_location_recommender/WALK_PLANNER_DESIGN.md) is the
original design note, kept for history. These links work only inside a research-repo checkout: the files are not
part of this package. [docs/HANDOFF.md §1.2](docs/HANDOFF.md#12-files-that-live-outside-this-package) lists them,
with the design canvas and the CI workflow, and [docs/SPEC.md](docs/SPEC.md#index-of-user-test-ids) gives the
English title of every WP-xx item the documents cite.
