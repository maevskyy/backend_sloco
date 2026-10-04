# Walk Planner: backend handoff (START HERE)

**Date:** 2026-10-03
**From:** data/research side (`sloco_recommendation_system` research repo)
**For:** the backend developer of `backend_sloco` (gateway, Supabase, server)
**Version:** walk-planner 1.0.0 · API v1 · data bundle `bucharest-20261002-d68a311e`

The Walk Planner plans walking routes through a city: real places, ordered and timed, with opening hours,
street legs and navigation links. It ships as a separate internal Python microservice, `walk-planner`, that
the gateway calls. This document lists what you receive, what to do in which order, and how to tell that
each step is done. Read [../README.md](../README.md) (10 minutes) and [SPEC.md](SPEC.md) §2–§5 first.

---

## 0. TL;DR

- **What runs:** one container `walk-planner` (FastAPI, 2 uvicorn workers, port 8000, internal, no auth).
  Next to it runs `osrm-foot` (self-hosted street router), with the ORS cloud as fallback and an optional
  Redis. It reads one read-only data bundle per city. Users, favourites and Supabase stay on your side.
- **How the gateway uses it:**
  - `POST /v1/walks/plan` returns route variants;
  - `POST /v1/walks/schedule` and `POST /v1/walks/insert` handle edits; the client sends back the
    `request` echo and a variant's `sequence`, and the service keeps no state;
  - `GET /v1/walks/places/search` and `GET /v1/walks/places/{place_id}` serve search and the place screen;
  - `GET /v1/walks/config` gives the form data.

  Place ids are strings (Google CID; they exceed int64). Details: [API.md](API.md),
  [INTEGRATION.md](INTEGRATION.md).
- **How you know it works:**
  - `golden run --url` replays 26 recorded scenarios (plans plus their edit chains) against your
    deployment and must print `26 pass`;
  - then you switch street routing on and check that plans report `routing: "streets"`.
- **Decisions needed** (§4.1): Supabase probably lacks about 5,624 of the 12,961 walk places; a new
  production ORS key on the `api.heigit.org` host; photo serving; anonymous access. §4.2 lists the known
  defects of the package's own files (generated schema, comments, scripts), each with its workaround.
- **Before you start:** check that §1.1 names a release (tag, commit, archives with sha256). On 2026-10-03 it
  was not cut yet.

---

## 1. What we deliver

| # | Deliverable | Where (paths relative to `services/walk_planner/`) | State on 2026-10-03 |
|---|---|---|---|
| 1 | Python package `walk_planner` 1.0.0 (algorithm, data access, personalization, routing, API views, CLI) | `walk_planner/` | 663 tests pass (macOS, Python 3.13, real data present). The CI test job replayed in a linux/amd64 container with the pinned locks: 632 passed, 31 skipped (the real-data tests) |
| 2 | The service (FastAPI app, schemas, settings, JSON logs) | `walk_planner/service/`; schema `docs/openapi.json` | All 9 endpoints. Golden replay over HTTP is identical |
| 3 | Image recipe | `deploy/Dockerfile`, `deploy/docker-entrypoint.sh`, `deploy/uvicorn-log-config.json`, hash-pinned `deploy/requirements*.lock` | Builds; 905 MB unpacked, about 200 MB compressed; runs as uid 10001 |
| 4 | Compose stack and environment template | `deploy/docker-compose.yml` (walk-planner, osrm-foot, redis profile `cache`), `deploy/env.example` | Verified on Docker Desktop with a real OSRM dataset |
| 5 | OSRM dataset script | `deploy/osrm/prepare_osrm.sh` | Verified: Bucharest clip in 116 s, including the 321 MB download; rebuild as a non-root user verified on Linux file semantics |
| 6 | Data bundle, Bucharest | `bucharest-20261002-d68a311e`: 12,961 places, 60.0 MB, 7 files; sync with `deploy/sync_bundle.sh` | Deep validation OK, all 78,620 photo keys have a file |
| 7 | Card photos | 78,620 JPEGs, 31.4 GB, on the research machine; sync with `deploy/sync_bundle.sh --photos`; nginx example `deploy/photos.nginx.conf.example` | Serving not set up on the server yet |
| 8 | Golden acceptance | `golden/scenarios.json`, `golden/expected/bucharest-20261002-d68a311e/` (26 scenarios: 24 baseline scenarios plus 2 with favourites), `python -m walk_planner golden run --url` | 26/26 against the package, local uvicorn and the image |
| 9 | Docs | [SPEC](SPEC.md), [API](API.md), [messages](messages.md) (+ `messages.json`), [ALGORITHM](ALGORITHM.md), [DATA](DATA.md), [ROUTING](ROUTING.md), [INTEGRATION](INTEGRATION.md), [DEPLOY](DEPLOY.md), [RELEASE](RELEASE.md), [CLI](CLI.md), [CHANGELOG](../CHANGELOG.md) | — |
| 10 | CI | `.github/workflows/walk-planner-ci.yml` in the research repo: tests on Python 3.12 with the locks; image build and smoke test; mini golden replay against the container. A copy ships as `deploy/ci/walk-planner-ci.yml` (§1.2) | Each step was run locally |

The package is self-contained, with no imports from research code. It can be built from a checkout of the
research repo or copied into `backend_sloco` (for example as `services/walk-planner/`); see §4, question 7.
Bundles are built on our side, because building them needs the research data (catalog, embeddings,
photos). You only sync and mount them.

### 1.1 Release identity

Every command below refers to one release. These are its identifiers. The research side fills in the open
values when it tags the release, before it sends this handoff ([RELEASE.md §4](RELEASE.md#4-release-procedure-research-repo),
steps 8–10).

| What | Value |
|---|---|
| Version | `1.0.0` (`walk_planner/version.py`) |
| Git tag (research repo) | not tagged in this delivery |
| The image's `GIT_SHA` | `1.0.0-20261003` (delivery label) |
| Source archive | `walk-planner-1.0.0.tar.gz`, sha256 in `walk-planner-1.0.0.tar.gz.sha256` (sent with it) |
| Bundle archive | `bucharest-20261002-d68a311e.tar.gz`, sha256 in `bucharest-20261002-d68a311e.tar.gz.sha256` (sent with it), or sent with `deploy/sync_bundle.sh` (step 4) |
| Image in a registry (optional) | `ghcr.io/<org>/sloco-walk-planner:1.0.0`, if the research side publishes one |

**Delivered on 2026-10-03** as the two archives above with their `.sha256` files. Check them with
`sha256sum -c <file>.sha256` before unpacking, and build the image with `GIT_SHA=1.0.0-20261003`.

### 1.2 Files that live outside this package

The documents reference the design canvas and some files of the research repo. None of them is in the package
or its archive, so the research side sends them, or access to them, with the handoff:

| What | Why you need it | Without it |
|---|---|---|
| Design canvas «SLOCO · Route Planner» (a claude.ai design artifact) | the screens of the app | [INTEGRATION.md §5](INTEGRATION.md#5-screen-by-screen-mapping) maps every artboard to API fields; ask for access (§4.1, question 15) |
| ~~`WALK_PLANNER_UX_BRIEF.md`~~ — now shipped as [docs/reference/UX_BRIEF_ru.md](reference/UX_BRIEF_ru.md) (Russian) | the brief the screens were designed from (§10 screens, §12 out of scope) | — |
| ~~`WALK_PLANNER_UX_TEST_REPORT.md`~~ — now shipped as [docs/reference/UX_TEST_REPORT_ru.md](reference/UX_TEST_REPORT_ru.md) (Russian); the raw persona reports `walk_planner_ux_test/` stay in the research repo | the evidence behind the WP-xx and I-x ids in these documents | [SPEC.md](SPEC.md#index-of-user-test-ids) lists every id with its English title |
| ~~`.github/workflows/walk-planner-ci.yml`~~ — a copy ships as [deploy/ci/walk-planner-ci.yml](../deploy/ci/walk-planner-ci.yml) | the CI of the package (tests with the locks, image smoke test, mini acceptance) | adjust its `paths:` / `working-directory` to where you vendor the package ([DEPLOY.md §14](DEPLOY.md#14-ci)) |
| `docs/handoffs/2026-07-16-onboarding-backend-endpoints.md` | where `place_id = places.source_id`, the `feed_places_by_source_ids` RPC pattern and `getSavedSignals()` come from | [INTEGRATION.md §3.2](INTEGRATION.md#32-id-mapping-sourceid--placeid) states what it says and what is unverified |

---

## 2. Your checklist

Run the steps in order. Commands assume the package root on the server (written `$WP`): a checkout of
`services/walk_planner`, the unpacked release, or your vendored copy. They also assume the default host
paths of the compose file. A step is done when its **DoD** (definition of done) holds.

[DEPLOY.md](DEPLOY.md) §6 is the same deployment as a command-by-command runbook. It builds the OSRM
dataset after the golden acceptance instead of before it. Either order works, because the acceptance runs
without street routing. The gateway and app checklists are in [INTEGRATION.md](INTEGRATION.md) §8.

### Step 1. Host resources

| Need | Amount | Source |
|---|---|---|
| CPU | walk-planner 2 (one per worker), osrm-foot 1, Redis 0.5 (optional) | compose limits |
| Memory | walk-planner limit 3 GB (about 1.3 GB per worker in the worst burst); osrm-foot 2 GB; Redis 320 MB (optional) | compose limits, `deploy/env.example` |
| Memory to build OSRM data | Bucharest clip: under 1 GB. All of Romania: estimated 2.5–4 GB peak (the script caps each build at 6 GB) | `prepare_osrm.sh` header |
| Disk | image about 0.9 GB; bundles about 60 MB each, keep about 3; OSRM: about 1 GB free under `/opt/osrm` with `--bbox`, 4 GB without; photos 31.4 GB in total | measured / script headers |
| Software | Docker with Compose ≥ 2.24 (the compose file uses the long `env_file` syntax); for the scripts: bash, curl, md5sum, python3, rsync, sha256sum, coreutils `mv -T` (flock optional) | script headers |

A note on photos: as of 2026-08-01 the server already held about 20 GB of food and things-to-do photos,
and no sights or shopping photos. That state comes from the 2026-08-01 handoff and was not re-checked.

```bash
docker compose version            # >= 2.24
sudo install -d -m 755 -o "$USER" /opt/sloco-data/walk /opt/sloco-data/walk/bundles /opt/osrm
```

**DoD:** Compose ≥ 2.24; the three directories exist and are owned by the deploy user (mode 755); free
memory and disk cover the table.

### Step 2. Code on the server, image built

Get the release onto the server: a tagged checkout of the research repo, the release archive, or your
vendored copy ([RELEASE.md](RELEASE.md)). Check the archive against its sha256 from §1.1. Then build the image
tagged with the algorithm version, never `latest`. [DEPLOY.md](DEPLOY.md) §3 also covers a registry.

```bash
sha256sum -c walk-planner-1.0.0.tar.gz.sha256        # an archive: check it before you unpack it (§1.1)
cd "$WP"
GIT_SHA=<the 12-hex commit of §1.1>                  # NOT `git rev-parse HEAD` of the repo you happen to be in
: "${GIT_SHA:?set GIT_SHA first}"
docker build -f deploy/Dockerfile --build-arg GIT_SHA="$GIT_SHA" \
  -t sloco-walk-planner:1.0.0 -t sloco-walk-planner:sha-"$GIT_SHA" .
docker run --rm sloco-walk-planner:1.0.0 python -c "import walk_planner; print(walk_planner.__version__)"   # 1.0.0
```

`GIT_SHA` labels the image and becomes `git_sha` in `/v1/meta`, which is how a running service is traced back to
its source. Take it from §1.1 or the release notes. Inside a research-repo checkout that has the tag,
`git rev-parse --short=12 'walk-planner-v1.0.0^{commit}'` prints it. Outside a git checkout (an archive, a
vendored folder) `git rev-parse` fails, and inside another repository it prints the wrong commit. An empty value still builds, but
tags the image `sha-` and leaves `git_sha` empty.

The build needs Docker Hub (base image `python:3.12.15-slim-trixie`) and PyPI. All dependencies are
hash-pinned.

**DoD:** the image exists as `sloco-walk-planner:1.0.0` and prints `1.0.0`;
`docker inspect -f '{{.Config.User}}' sloco-walk-planner:1.0.0` prints `10001:10001`;
`docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' sloco-walk-planner:1.0.0` prints
the commit of §1.1.

### Step 3. OSRM dataset (`deploy/osrm/prepare_osrm.sh`)

```bash
cd "$WP"
deploy/osrm/prepare_osrm.sh --bbox 25.80,44.20,26.40,44.70     # Bucharest clip (verified)
# or all of Romania (any Romanian city; heavier build):  deploy/osrm/prepare_osrm.sh
```

Run it as the non-root owner of `/opt/osrm`. The script downloads the Geofabrik extract only when it
changed and checks its md5, then clips, extracts, partitions and customizes. It makes the dataset
world-readable, then starts a canary `osrm-routed` hardened exactly like the compose service, routes a
smoke route through it, and switches `/opt/osrm/current` to the new dataset atomically. It is idempotent:
a re-run with an unchanged extract prints "up to date" and exits 0. Run it monthly from cron. Once the
stack is running, add `--compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env`
so it also recreates `osrm-foot` after a switch, and switches back if `osrm-foot` is not healthy. If you
merge the services into the backend's compose (step 6), pass that compose file instead, plus its own env file
if it needs one ([DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network)).
`--rollback` returns to the previous dataset. Exit codes and options are listed in the script header
(`--help`). Which extract to use is §4, question 5.

**DoD:** exit 0; the log shows `canary ok: smoke route legs: ...`; `readlink /opt/osrm/current` points to
`datasets/<id>`, e.g. `romania-clip-261001`.

### Step 4. Data bundle (`deploy/sync_bundle.sh`)

Whoever holds the bundle directory runs this, normally the data/research side, from its machine (60 MB):

```bash
SLOCO_SSH=deploy@<server> deploy/sync_bundle.sh <path>/bucharest-20261002-d68a311e --validate
```

The script checks size and sha256 of every file locally. It uploads to `.incoming-<id>`, verifies with
`sha256sum -c` on the server, sets modes 755/444 and publishes with one `mv -T` to
`/opt/sloco-data/walk/bundles/<id>/`. It never overwrites an existing bundle: an identical copy exits 0,
a different one exits 4. If the bundle arrives as an archive instead, unpack and validate it on the server
as in [DEPLOY.md](DEPLOY.md) §6, step 5.

**DoD:** `/opt/sloco-data/walk/bundles/bucharest-20261002-d68a311e/manifest.json` exists; files are mode 444;
a second run reports the bundle as identical and exits 0.

### Step 5. `walk.env` (secrets and settings)

```bash
install -m 600 "$WP/deploy/env.example" /opt/sloco-data/walk/walk.env      # then edit it
```

Set at least the following. Every other variable is documented in the file itself. This table is for the
standalone compose file (attach mode); when you merge the services into the backend's compose, `walk.env`
takes different keys ([DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network)).

| Variable | First start (acceptance, step 7) | Production |
|---|---|---|
| `WALK_BUNDLE_ID` | `bucharest-20261002-d68a311e` | same |
| `WALK_ROUTER_URL` | present and **empty** (`WALK_ROUTER_URL=`): no OSRM. Removing the line is not the same: unset means `osrm-foot` | line removed: defaults to `http://osrm-foot:5000` |
| `ORS_API_KEY` | empty | the **new production key** (below) |
| `PHOTO_BASE_URL` | empty | e.g. `https://<host>/walk-media` (step 9) |
| `ORS_MAX_PER_MIN` / `ORS_MAX_PER_DAY` | 17 / 900 (template) | per worker: keep workers × value under the key's plan |
| `WALK_PLANNER_TAG` | `1.0.0` (the image built in step 2) | the release tag |

**New ORS key.** Create a key used only by production, not the research dashboard's key, so testers cannot
drain the fallback quota. Keys work on `https://api.heigit.org/openrouteservice`, which is already the
service's default `ORS_BASE_URL`. The old host `api.openrouteservice.org` is deprecated and being shut down;
do not use it. The plan limits assumed by `deploy/env.example` are 40 requests per minute and 2,000 per day
per key (the free plan). Keep the key only in `walk.env`: never under compose `environment:`, never on a
command line. `docker compose config` prints secrets, so do not paste its output anywhere.

**Another path for `walk.env`.** The compose file finds the container's `env_file` through `WALK_ENV_FILE`,
default `/opt/sloco-data/walk/walk.env`. If your `walk.env` lives elsewhere, add `WALK_ENV_FILE=<its absolute path>`
to the file itself (`--env-file` reads it). Otherwise compose stops with `env file /opt/sloco-data/walk/walk.env
not found`. `deploy/env.example` does not list this variable yet (§4.2).

**DoD:** `walk.env` has mode 600 and the values above;
`docker compose --env-file /opt/sloco-data/walk/walk.env -f "$WP/deploy/docker-compose.yml" config --quiet`
exits 0. Run it with `--quiet`, so it prints nothing.

### Step 6. Compose deployment, joined to the backend network

There are two ways to join the backend. [DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network) has both recipes;
both were tried with Docker Compose on 2026-10-03.

- **Attach** (the simpler one, and what the commands of this checklist assume). Keep the compose file standalone
  and add only `walk-planner` to the backend's external network with a small override file.
- **Merge.** Copy the `walk-planner` and `osrm-foot` services, plus the `x-logging` anchor, into
  `backend_sloco`'s compose file. They join its default network. **Delete the copied `environment:` block** and
  put its values into `walk.env` (`WALK_BUNDLE_DIR=/bundles/<bundle id>`, `WALK_ROUTER_URL=http://osrm-foot:5000`,
  …). If you keep the block, its `${…}` values are filled from the backend's own env, override `walk.env`, and
  silently switch OSRM on or the photo URLs off. The compose file's header suggests keeping `walk.env` "under
  env_file" instead: that alone is not enough (§4.2).

Either way the gateway reaches `http://walk-planner:8000`; set `WALK_PLANNER_URL=http://walk-planner:8000` on it.
Never publish the ports of `osrm-foot` or Redis. Start only the planner first:

```bash
docker compose --env-file /opt/sloco-data/walk/walk.env -f "$WP/deploy/docker-compose.yml" up -d walk-planner
docker compose --env-file /opt/sloco-data/walk/walk.env -f "$WP/deploy/docker-compose.yml" ps
curl -fsS http://127.0.0.1:18600/v1/health/ready
```

If the container exits with code 78, the entrypoint's bundle check failed: the bundle is missing,
corrupt, or not mounted. The log names the cause. The service answers `/v1/health/ready` about 5–6 s after
`up -d` (5.6 s measured on Docker Desktop with 2 workers). `docker compose ps` shows `healthy` only after the
image's first health check, about 30 s after start: a gateway that waits with `depends_on:` and
`condition: service_healthy` waits that long (§4.2).

**DoD:** `walk-planner` is `healthy`; `/v1/health/ready` returns
`{"status":"ready","bundles":["bucharest-20261002-d68a311e"]}`; the gateway container reaches
`http://walk-planner:8000/v1/health/ready`.

### Step 7. Acceptance: golden replay without routing, then routing on

**7a. Identity.**

```bash
curl -fsS http://127.0.0.1:18600/v1/meta \
  | jq -c '{version, state, bundles: [.bundles[].bundle_id], chain: .routing.chain, photo: .settings.photo_base_url}'
# {"version":"1.0.0","state":"ready","bundles":["bucharest-20261002-d68a311e"],"chain":["estimate"],"photo":null}
```

**7b. Golden replay.** The golden outputs were recorded with straight-line routing and without photo URLs,
so this step needs the configuration from step 5's first-start column. The image does not contain the
golden files; mount them from the checkout:

```bash
cd "$WP"
docker run --rm --network host -v "$PWD/golden:/golden:ro" sloco-walk-planner:1.0.0 \
  python -m walk_planner golden run --url http://127.0.0.1:18600 \
  --scenarios /golden/scenarios.json --expected-root /golden/expected
```

The replay sends every recorded call: 26 plans and the schedule/insert calls of their edit chains. Each
call carries `X-Request-Id: golden-<scenario>-0` (the plan) or `golden-<scenario>-<chain><step>` (an edit, e.g.
`golden-S01_default-A0`), so it can be found in the service log. If you removed
the loopback port, use `--network <backend network>` and `--url http://walk-planner:8000` instead. On Docker
Desktop, which has no host network, drop `--network host` and use `--url http://host.docker.internal:18600`.
It takes 20–25 s.

- Exit 0: every scenario passed.
- Exit 1: a difference, a missing expected set, or a city the service does not serve. Send us the output.
- Exit 2: the service is unreachable or not ready.
- A `WARNING` line means `/v1/meta` shows street routing or a `PHOTO_BASE_URL`. Its advice "restart the service
  without WALK_ROUTER_URL" means, under compose, `WALK_ROUTER_URL=` set **empty** in `walk.env`, because unset
  means `osrm-foot`. A WARNING with exit 1 is expected: fix the configuration and replay. A WARNING with exit 0
  means routing is configured but no router answered, so every leg fell back to the estimate: the planner is
  accepted, but your routing does not work yet (7c).

**7c. Routing on.** In `walk.env` remove the `WALK_ROUTER_URL=` line (merged compose: write
`WALK_ROUTER_URL=http://osrm-foot:5000` instead, [DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network)) and set
`ORS_API_KEY`. Then start both services and request a plan:

```bash
docker compose --env-file /opt/sloco-data/walk/walk.env -f "$WP/deploy/docker-compose.yml" up -d walk-planner osrm-foot
curl -s -X POST http://127.0.0.1:18600/v1/walks/plan -H 'Content-Type: application/json' \
  -d '{"city": "Bucharest", "date": "2026-10-03", "start_time": "10:00", "end_time": "14:00",
       "shape": "loop", "start": "city_center", "slots": ["sight", "coffee", "park", "food"]}' \
  | jq -c '{routing: [.variants[].summary.routing], dataset: .versions.routing}'
# {"routing":["streets","streets","streets"],"dataset":"<the OSRM dataset id, e.g. romania-clip-261001>"}
```

Optionally stop `osrm-foot` and repeat the request. It must still answer: legs come from ORS, or from the
estimate with a `routing_estimate` message. This spends ORS quota.

Note the `X-Process-Time-Ms` of a few plans and compare them with the "Image" column of
[SPEC.md §10](SPEC.md#10-performance-and-capacity) (a 4-hour plan about 0.43 s, a 24-hour plan about 4 s on a
laptop's Docker). The production host has not been measured yet.

**DoD:**
- 7a matches.
- 7b prints `26 scenarios: 26 pass` and exits 0.
- After 7c, `/v1/meta` shows `routing.chain` = `["osrm","ors","estimate"]`, every variant reports
  `routing: "streets"` and `versions.routing` is the OSRM dataset id.

### Step 8. Gateway endpoints ([INTEGRATION.md](INTEGRATION.md) is the contract)

[INTEGRATION.md](INTEGRATION.md) defines the six app-facing routes. They map 1:1 onto the service, with
camelCase and `sourceId` at the gateway. It also sets their auth, timeouts, retries, rate limits, caching
and key conversion. The points that break things when missed:

- **Ids.** `place_id` is the Google CID as a decimal string of up to 20 digits, beyond int64. It must stay a
  string end to end: JSON, TypeScript and Postgres `text`. It should equal Supabase `places.source_id`; that,
  the `feed_places_by_source_ids` RPC and `getSavedSignals()` come from our earlier onboarding handoff and are not
  verified against your code ([INTEGRATION.md §3.2](INTEGRATION.md#32-id-mapping-sourceid--placeid) says what
  the gateway needs from them). A place without a Supabase row gets `placeId: null` (§4, question 1).
- **Favourites.** The gateway fills `favourite_place_ids` and `want_to_go_place_ids` from the user's saved
  places, at most 500 ids together. The app never sends them. Without favourites the plan uses popularity.
- **Editing is stateless.** Pass the plan's `request` echo and `variants[i].sequence` through unchanged to
  `/schedule` and `/insert`. The server rebuilds everything else.
- **Timeouts and retries** ([INTEGRATION.md](INTEGRATION.md) §3.5):
  - `/plan` 20 s, never below 15 s, because the routing budget alone is 6 s;
  - retry only on 503 `busy` (`Retry-After: 2`), 503 `not_ready` (`Retry-After: 5`) or a refused
    connection;
  - never re-send a plan after a timeout, and never retry a 4xx.
- **Errors** pass through in the service's envelope `{"error": {code, message, params}}`, with the codes in
  [messages.json](messages.json). On insert, 422 `place_temporarily_closed` means: ask the user, then
  resend with `allow_temporarily_closed: true`.
- **Request ids.** Forward `X-Request-Id`; the service logs it on every line of that request.

**DoD:** the gateway part of [INTEGRATION.md](INTEGRATION.md) §8 is checked off. That includes the
key-conversion round trip over the golden requests, the busy and timeout tests, and the end-to-end S01
plan → schedule → insert → reset.

### Step 9. Photos ([DATA.md](DATA.md))

1. Sync the photo tree to `/opt/sloco-data/visual_photo_profiles/photos_cid`. The data side runs this
   from the research machine; it is incremental: `SLOCO_SSH=deploy@<server> deploy/sync_bundle.sh --photos`.
2. Add the `location ^~ /walk-media/` block from `deploy/photos.nginx.conf.example` to the HTTPS vhost
   that should serve photos. Then run `nginx -t && systemctl reload nginx`.
3. Set `PHOTO_BASE_URL=https://<host>/walk-media` in `walk.env` and run `up -d walk-planner`. From then on,
   golden replays against this service report differences; use the temporary acceptance container of
   [DEPLOY.md §7.1](DEPLOY.md#71-switch-to-a-new-bundle) for later replays.
4. Recommended before launch: resized variants (WebP 480 px and 1080 px are suggested in the nginx example)
   or a resizing CDN behind the same prefix. Originals average 400 KB, and a phone card needs about
   50–150 KB.

**DoD:**
- `curl -sI https://<host>/walk-media/photos_cid/<cid>/00_all.jpg` returns 200 `image/jpeg`.
- The directory URL `.../photos_cid/` returns 404.
- The card photo `url`s in a plan response load in a browser.
- This command reports `existing=78620, missing=0`:

```bash
docker run --rm -v /opt/sloco-data:/opt/sloco-data:ro sloco-walk-planner:1.0.0 \
  python -m walk_planner bundle validate --photos-root /opt/sloco-data/visual_photo_profiles/photos_cid \
  /opt/sloco-data/walk/bundles/bucharest-20261002-d68a311e
```

### Step 10. Supabase coverage decision

5,624 of the bundle's 12,961 places are not in the production recommender catalog
(`locations_combined_food_ttd.csv`, 12,578 rows): all 2,507 sights, all 569 shopping places, 2,090
food & drink and 458 things to do. We measured this on 2026-10-03 against the local copy of that file. If
Supabase `places` mirrors that catalog, these places have no `places.id`. Without one they cannot be
saved, found by `GET /v1/search/places`, or opened in the existing place screen. Two of the four default
slots (sight, park) draw only from the missing groups. This has not been verified against Supabase. Count it: [DATA.md §6.3](DATA.md#63-supabase-coverage-gap) exports
the 12,961 ids with their theme group (with a `docker run` of the service image) and gives the SQL that counts
the coverage per group.

Options:

- **(a) Import** the missing places into `places` with `source_id = CID`. This is needed for saving a walk
  stop, for the existing place screen and for the existing search.
- **(b) Interim for v1.** Walk screens use the planner's own place data:
  - `places{}` cards in plan and edit responses;
  - `GET /v1/walks/places/search` for start, must-visit and add-place;
  - `GET /v1/walks/places/{place_id}` for the place screen.

  App ids are null where Supabase has no row, and "save" is disabled there.

**DoD:** the coverage count is recorded; the product owner has chosen (a) or (b); the gateway handles a CID
without a Supabase row without errors.

### Step 11. App wiring per screen

The designed screens follow `WALK_PLANNER_UX_BRIEF.md` §10 (research repo,
`recommendation_system/ai_location_recommender/`; not in this package, §1.2). The app calls the gateway's
routes; the endpoint names below are the service's. [INTEGRATION.md](INTEGRATION.md) §5 maps each artboard to
fields, §6 gives the client state for editing, and §7 lists design elements v1 does not support. Questions 11–15
of §4.1 are open design points of these screens.

| Screen | Calls | Main fields |
|---|---|---|
| New walk, more options | `GET /v1/walks/config` once per city and language; `GET /v1/walks/places/search` for the start and must-visits; `POST /v1/walks/plan` | Codes and labels of activities, styles and shapes; `dwell_choices`, `defaults`, `limits`. Start: device location `{lat, lon}` or a found place `{place_id}`; none for `free` |
| Variants | the plan response | Per variant `summary`: `stops_total`, `distance_km`, `finish_at.local`, `slack_min`, `over_budget`, `routing`; photos from `places{}`; badges from variant messages |
| Route (timeline and map) | the plan response | `stops` (TimePoints, `dwell_min`, `wait_min`, `hours.status`, `kind`, `business_status`); `segments` (`walk_min`, `distance_m`, `depart` / `arrive`, geometry; draw `quality: "estimate"` legs as estimates); `navigation`; `bbox`; messages by scope |
| Edit | `POST /v1/walks/schedule`, `POST /v1/walks/insert` | Keep `request` and the current `sequence`. Reorder, remove, restore and minutes (5–480) go to `/schedule`; add goes to `/insert` (handle 409 and 422; temporarily closed: confirm, then resend). Reset and the "not visiting" bin are client-side |
| Place | `GET /v1/walks/places/{place_id}` | Up to 10 photos, type, rating, summary and text sections, tags, week hours, address, `google_maps_url` |
| Search | `GET /v1/walks/places/search` | Name, type, rating, address, one photo, temporarily-closed badge |
| States | — | `status` `no_candidates` / `no_route` (200, no variants, messages explain); 4xx codes from `messages.json`; 503 `busy`; loading (a 4 h plan takes 0.3–0.45 s, a 24 h plan 3–4 s, plus routing: [SPEC.md §10](SPEC.md#10-performance-and-capacity)) |

Attribution: show "© OpenStreetMap contributors" wherever routes are drawn, and the ORS string where ORS
legs are shown ([ROUTING.md](ROUTING.md) §4.2).

**DoD:** the app part of [INTEGRATION.md](INTEGRATION.md) §8 is checked off, tested both with routing off
(estimate) and on.

### Step 12. Monitoring

Each request writes one JSON line on stdout (logger `walk_planner.service.access`): `endpoint`, `status`,
`duration_ms`, `timings`, `routing_quality`, `messages` and the error code. Compose keeps these logs with
the json-file driver, 10 MB × 5. `GET /v1/meta` shows the routing chain, breaker states, ORS quota, leg
cache and load-guard counters. These figures are per worker, so poll it several times. There is no
`/metrics` endpoint in v1. [DEPLOY.md](DEPLOY.md) §11 has the full signal table with thresholds, and
[ROUTING.md](ROUTING.md) §10.3 has the routing alerts. At minimum, alert on:

- a container that is not healthy or keeps restarting (exit code 78 means a bundle problem);
- any 5xx (`internal_error`);
- a sustained rate of 503 `busy`;
- a rising share of plans whose `routing_quality` is not `streets` (OSRM down), and an open ORS breaker;
- plan p95 latency;
- memory close to the 3 GB limit;
- a failed monthly `prepare_osrm.sh` run.

**DoD:** these alerts exist and point to [DEPLOY.md](DEPLOY.md) §15 (troubleshooting).

---

## 3. How future versions arrive

See [RELEASE.md](RELEASE.md) for the full procedure.

- **What changes.** The code version (`ALGORITHM_VERSION`, which is also the image tag) and the data
  (`bundle_id`) change independently. Every release has an entry in [CHANGELOG.md](../CHANGELOG.md):
  - a PATCH changes no golden output;
  - a MINOR changes plans on purpose and ships a new expected golden set;
  - a MAJOR breaks the API contract.
- **What we send.** The tag to deploy with its commit and the archives' sha256 (as in §1.1), plus a new bundle
  id when the data changed (synced as in step 4). The release note states what changed in plans and API, and
  any new environment variables.
- **What you do:**
  1. Build the image for the new tag (step 2).
  2. Run it once as a temporary acceptance container, without `walk.env` (so no routing and no photo URLs), and
     replay the golden outputs against it: the three commands of [DEPLOY.md §7.1](DEPLOY.md#71-switch-to-a-new-bundle),
     step 2. It must print `26 scenarios: 26 pass`. The image's CI job does the same with the mini set.
  3. Set `WALK_PLANNER_TAG` and `WALK_BUNDLE_ID` in `walk.env` and run `up -d walk-planner`.
- **Rollback:** the previous `WALK_PLANNER_TAG` and `WALK_BUNDLE_ID`, then `up -d walk-planner`. For OSRM
  data, `prepare_osrm.sh --rollback`.
- **Planned content:** [SPEC.md](SPEC.md) §14 gives the stages from the user-test report.

---

## 4. Open questions for the product owner and the backend

### 4.1 Decisions

| # | Question | Owner | Default if nobody decides |
|---|---|---|---|
| 1 | Supabase: import the 5,624 missing walk places (a), or use the interim walk-planner cards (b)? | PO + backend | (b) for v1 |
| 2 | Anonymous users: may they plan walks? May onboarding picks act as favourites before sign-up? | PO | Plan without favourites |
| 3 | Texts: sign-off of the new v1 messages (RU drafts): `must_visit_closed_forever`, `place_temporarily_closed`, `routing_estimate`, `unknown_place_ids` and the new error texts. The app may also show its own copy per code | PO / designer | The service texts |
| 4 | ORS: who owns the production account and key; is the free plan (2,000/day, 40/min) enough as a fallback, or a paid plan; commercial-use terms; exact attribution text | PO + backend | Free plan, OSRM first |
| 5 | OSM extract: Bucharest clip (light, Bucharest only) or all of Romania (any Romanian city, heavier build)? | backend | Bucharest clip |
| 6 | Photos: public URLs or signed URLs; resizing or CDN before launch; rights to show third-party place photos | PO + backend | Public nginx prefix, originals |
| 7 | Delivery: copy the package into `backend_sloco` or build from a tagged research-repo checkout; registry or build on the server | backend | Build on the server from a tagged checkout |
| 8 | Gateway: rate limits per user / IP for plans and edits; timeouts; retries on `busy` | backend | The starting values of [INTEGRATION.md](INTEGRATION.md) §3.5–§3.6 |
| 9 | Production host capacity: worker count and CPU / memory once measured (step 7) | backend | 2 workers, 3 GB |
| 10 | Redis for the leg cache: the backend's Redis (its own database number) or none | backend | None (in-process cache per worker) |
| 11 | The "Until evening" preset of the form (`WPNew`): which end time it means | PO / designer | End at 20:00 the same day; hide the preset when that leaves less than an hour |
| 12 | Which dates the app may plan. The API accepts any valid date, past ones too, and plans it on the weekly opening hours ([API.md §3.5](API.md#35-post-v1walksplan)) | PO | The app offers today and later dates only; no server-side check |
| 13 | Badges on the variant cards (`WPVariants`): which messages and statuses become which badge | designer | The mapping proposed in [INTEGRATION.md §5.2](INTEGRATION.md#52-badges-on-the-variant-cards-proposal) |
| 14 | Opening hours in search rows: the design shows them, the API has none | PO / designer | No hours in search rows in v1; the place screen shows them |
| 15 | Access to the design canvas «SLOCO · Route Planner» (claude.ai design artifact `dc1b0efa-15b8-41dc-b46a-2bbdb39108ea`) for the backend and app developers, and its link in §1.2 | research side / designer | None: needed before the app work starts |

### 4.2 Known issues in the package's own files

Found in the handoff review of 2026-10-03. None of them changes a plan, so each fix is a PATCH release
([RELEASE.md §2](RELEASE.md#2-semantic-versioning-of-algorithm_version)). The documents already describe the real
behaviour, and each issue has a workaround. Owner: the research side.

| # | Issue | Effect | Workaround |
|---|---|---|---|
| 1 | 1.0.0 has no git tag in the research repo; this delivery is identified by the archives' `.sha256` files and the label `1.0.0-20261003` | `git archive` / `git rev-parse` commands of RELEASE.md do not apply to this delivery | §1.1 |
| 2 | `docs/openapi.json` is still looser than the service in places: nullable instead of absent keys, a few missing enums, undeclared headers (`X-Request-Id`, `X-Process-Time-Ms`). Fixed on 2026-10-03: EditStop `dwell_min` is required, `/plan` declares 404, `must_visit_place_ids` has no pre-de-duplication `maxItems` | a client generated from it may expect `null` where a key is absent | [API.md §4.0](API.md#40-schema-names-and-the-gaps-of-openapijson) lists the remaining gaps |
| 3 | The image's HEALTHCHECK has no start interval | compose reports `healthy` only after about 30 s | probe `/v1/health/ready` yourself; a later image can add `--start-interval` (Docker Engine 25+) |
| 4 | `ENVIRONMENT` defaults to `production` | a local or staging run reports `production` in `/v1/meta` | set `ENVIRONMENT` in every environment |
| 5 | The real-data tests find the Bucharest bundle only through the research repo's layout, with no variable to point them elsewhere | outside the research repo they skip (631 passed, 32 skipped) | `golden run --bundle <dir>` covers the same ground (README §6) |
| 6 | `present.START_LABEL["en"]` is never used | none: the API's navigation links carry no labels | none needed |
| 7 | With `personalization_strength: 0`, usable favourites are in neither `favourites_used` nor `favourites_ignored` | the response does not say which ids were usable | [API.md §4.6](API.md#46-personalization); decide whether to list them as used |
| 8 | Lenient inputs kept for compatibility: `start: {lat, lon, place_id}` does not check the id; a sequence item without `kind` becomes `pinned`; Russian activity labels are accepted (style and shape labels are not) | wrong inputs pass silently | send what the responses gave you, and codes; tightening waits for `/v2` ([RELEASE.md §8](RELEASE.md#8-compatibility-and-deprecation-policy)) |

Fixed after the review (2026-10-03), no longer open: `tools/export_openapi.py --check` ignores the fastapi/pydantic
version stamp; the `docs/messages.json` notes now say which Russian texts are drafts; the compose header's merge
step 4 matches [DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network); `deploy/env.example` lists `WALK_ENV_FILE`;
`golden/README.md` and the `golden run --url` warning say "set WALK_ROUTER_URL / ORS_API_KEY / PHOTO_BASE_URL
**empty**" and give the real request ids; the Dockerfile header takes `GIT_SHA` from the release tag's commit; the
CI workflow ships as `deploy/ci/walk-planner-ci.yml`.

## 5. Who to ask

| Topic | Ask |
|---|---|
| Algorithm, data bundles, golden outputs, this package, the deploy recipes | the data/research side: the sender of this handoff and owner of the research repo |
| Screens and UX copy | the designer, who worked from `WALK_PLANNER_UX_BRIEF.md` |
| Product decisions (§4) | the product owner |
| A failing golden replay | send us the full `golden run --url -v` output, the `/v1/meta` output and the image tag |
