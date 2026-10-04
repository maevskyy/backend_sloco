# Walk Planner: deployment runbook

How to build, run, check, operate and roll back the `walk-planner` service on the backend host. The
ordered handoff checklist is [`HANDOFF.md`](HANDOFF.md). Wiring the gateway and the app:
[`INTEGRATION.md`](INTEGRATION.md). Versions and upgrades: [`RELEASE.md`](RELEASE.md).
All commands run from the **package root**: the folder with `deploy/`, `walk_planner/` and `golden/`. That
is `services/walk_planner` in the research repo, or wherever the release was unpacked or vendored (for
example `backend_sloco/services/walk-planner`).

The numbers marked *measured* come from a MacBook (Docker Desktop, arm64 VM) with the Bucharest bundle,
not from the production host. Re-check them there with `docker stats` and the access log.

## 0. What runs where

| Service (compose) | Image | Port | Mounts | Limits |
|---|---|---|---|---|
| `walk-planner` | `sloco-walk-planner:<version>`, built from `deploy/Dockerfile` | `:8000` on the Docker network; host `127.0.0.1:18600` for checks | `/bundles` ← `/opt/sloco-data/walk/bundles` (read-only) | 2 CPU, 3 GB |
| `osrm-foot` | `ghcr.io/project-osrm/osrm-backend:v26.10.0-debian` | `:5000` on the Docker network only | `/data` ← `/opt/osrm/current` (read-only) | 1 CPU, 2 GB |
| `redis` (profile `cache`, optional) | `redis:7.4.11-alpine` | `:6379` on the Docker network only | none (a pure cache: no persistence) | 0.5 CPU, 320 MB |

| Host path | Contents |
|---|---|
| `/opt/sloco-data/walk/walk.env` | the environment file, mode 600 (holds `ORS_API_KEY`) |
| `/opt/sloco-data/walk/bundles/<bundle_id>/` | data bundles, immutable (dirs 755, files 444); keep about 3 |
| `/opt/osrm/` | `downloads/`, `datasets/<id>/`, `logs/`, and the symlinks `current` and `previous` |
| `/opt/sloco-data/visual_photo_profiles/photos_cid/` | card photos, served by nginx (§9) |
| the release folder, e.g. `/opt/walk-planner/current` | `deploy/` scripts and `golden/` acceptance data |

The service is stateless. It holds no user data, writes nothing outside `/tmp`, and needs no database.

## 1. Prerequisites

### 1.1 Host sizing

| | Needed | Measured / basis |
|---|---|---|
| CPU | 2 cores for `walk-planner` (one per uvicorn worker), 1 for OSRM | Plans are CPU-bound: in the image a 4-hour plan takes about 0.43 s of one core and a 24-hour plan about 4 s ([SPEC.md §10](SPEC.md#10-performance-and-capacity)). |
| RAM | about 4 GB free | `walk-planner` idle: about 0.5 GB per worker plus 0.13 GB for the supervisor (0.9 GB with 2 workers); peak with the default load guard: 1.1 GB (*measured*, bursts of 30 large plans). OSRM with the Bucharest clip: a few hundred MB; Romania-wide: about 1 GB (estimate). Compose limits add up to 5.3 GB, but they are caps, not reservations. |
| Disk | about 5 GB free under `/opt`, plus the photos | image 905 MB (200 MB compressed); OSRM image 411 MB; osmium build image 147 MB; bundles about 60 MB each (keep 3); OSRM: download cache 321 MB (`romania-latest.osm.pbf`), Bucharest-clip datasets about 108 MB each (current, previous and 2 older); logs 5 × 10 MB per container. Photos: §9. |
| OSRM build | under 1 GB RAM and about 1 minute for the Bucharest clip; estimated 2.5–4 GB for Romania-wide | `prepare_osrm.sh` caps each build container at `--build-mem 6g`. |

### 1.2 Software

- Linux, x86_64 or arm64: the lock files carry wheels for both.
- Docker Engine 24 or newer, and the Compose plugin **2.24 or newer** (needed for `env_file` with
  `required:`).
- `bash`, `curl`, `md5sum`, `python3` (used by `prepare_osrm.sh`), GNU coreutils (`mv -T` in §6 step 5; macOS's
  `mv` has no `-T`), optionally `flock`. On the machine that ships bundles and photos: `rsync` and `ssh`. The
  existing host nginx serves the photos.
- A non-root deploy user in the `docker` group. It owns `/opt/osrm` and `/opt/sloco-data/walk`.
- Outbound HTTPS:
  - when building: PyPI, Docker Hub;
  - for OSRM data: `ghcr.io`, `download.geofabrik.de`, Debian mirrors (the osmium image);
  - at run time: only `api.heigit.org`, if the ORS fallback is configured.
- Inbound: nothing. The service binds `127.0.0.1:18600` only.

## 2. Files in `deploy/`

| File | Purpose |
|---|---|
| `Dockerfile` | Two stages on `python:3.12.15-slim-trixie`. Stage 1 builds the `walk_planner` wheel; stage 2 installs the hash-pinned runtime lock and the wheel, runs as uid/gid 10001, serves `:8000`, and its HEALTHCHECK calls `/v1/health/ready`. The context is the package root, filtered by `.dockerignore`. |
| `docker-entrypoint.sh` | Before `uvicorn` starts, validates every bundle of `WALK_BUNDLE_DIR` (manifest, sizes, sha256, schema, alignment; about 1.3 s). On failure the container exits **78**. Any other command runs as is. |
| `docker-compose.yml` | `walk-planner` and `osrm-foot`, plus `redis` (profile `cache`). Hardened (§13). The header explains how to merge it into the backend's compose. |
| `env.example` | Template of `walk.env`; every variable is documented (§5). |
| `requirements.lock` | Runtime dependencies: 30 pins, all with hashes, CPython 3.12, Linux x86_64 and aarch64. numpy and pandas are pinned on purpose: the golden outputs depend on them. |
| `requirements-test.lock`, `requirements-build.lock` | pytest + httpx (CI) / setuptools (wheel stage only) |
| `uvicorn-log-config.json` | Makes uvicorn's own log lines JSON too. The image's CMD uses it. |
| `osrm/prepare_osrm.sh` | Builds and refreshes the OSRM foot dataset: Geofabrik extract with md5 check → optional `--bbox` clip → extract, partition, customize → a hardened canary → atomic switch of `current`/`previous` → recreate `osrm-foot`. Also `--rollback`. Exit codes 0, 1 (unexpected error) and 2–7 (`--help`). |
| `sync_bundle.sh` | Uploads a bundle from the research machine: local sha256 check, rsync to `.incoming-<id>`, `sha256sum -c` on the server, permissions 755/444, then an atomic `mv`. Never overwrites a bundle. `--photos` syncs the photo tree instead. |
| `photos.nginx.conf.example` | nginx `location` for `/walk-media/photos_cid/<cid>/<NN>_<label>.jpg` |

Also: `.dockerignore` at the package root, and the research repo's `.github/workflows/walk-planner-ci.yml`
(§14), which is not part of the package.

## 3. Build the image

The image holds code only. Bundles are mounted, and secrets arrive as environment variables. Tag it with
the algorithm version and the git sha; never deploy `latest`.

```bash
VERSION=$(grep -oE 'ALGORITHM_VERSION = "[^"]+"' walk_planner/version.py | cut -d'"' -f2)   # e.g. 1.0.0
GIT_SHA=<12-char research-repo commit of the release tag>     # HANDOFF.md §1.1 or the release notes
: "${GIT_SHA:?set GIT_SHA first}"
docker build -f deploy/Dockerfile --build-arg GIT_SHA="$GIT_SHA" \
  -t sloco-walk-planner:"$VERSION" -t sloco-walk-planner:sha-"$GIT_SHA" .
docker run --rm sloco-walk-planner:"$VERSION" python -c "import walk_planner; print(walk_planner.__version__)"
```

- **`GIT_SHA`** is the commit of the release tag in the research repo. It becomes the image's
  `org.opencontainers.image.revision` label and `git_sha` in `/v1/meta`. From an unpacked archive or a vendored
  folder, take it from [HANDOFF.md §1.1](HANDOFF.md#11-release-identity) or the release notes. Only a research-repo
  checkout with the tag can compute it: `git rev-parse --short=12 "walk-planner-v$VERSION^{commit}"`. Do not use
  `git rev-parse --short HEAD`, as the Dockerfile's header does: outside a git checkout it fails, and in another
  repository (or on another commit) it names the wrong source. An empty `GIT_SHA` still builds, but tags the image
  `sha-` and leaves `git_sha` empty; the `:?` guard above stops that.
- The build took about 50 s (*measured*). It needs no compiler: wheels only, every hash checked
  (`pip install --require-hashes --only-binary=:all:`).
- For a bit-identical rebuild, pin the base by digest:
  `--build-arg PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256`.
  Debian republishes security fixes under the same tag, so rebuild regularly and run the acceptance (§6
  step 9) afterwards.
- The same build through compose: set `WALK_PLANNER_TAG` and `GIT_SHA` in `walk.env`, then run
  `wpc build walk-planner` (§4.1).

**No registry (build on the host).** Unpack the release on the server and build there. Keep the previous
tag: do not run `docker image prune -a`. It is your rollback.

**GitHub Container Registry.** Build once, pull on the server:

```bash
docker buildx build --platform linux/amd64 -f deploy/Dockerfile --build-arg GIT_SHA="$GIT_SHA" \
  -t ghcr.io/<org>/sloco-walk-planner:"$VERSION" --push .
# on the server (a token with read:packages, never on the command line):
docker login ghcr.io -u <user> --password-stdin < ~/.ghcr_token
# walk.env: WALK_PLANNER_IMAGE=ghcr.io/<org>/sloco-walk-planner  WALK_PLANNER_TAG=<version>
wpc pull walk-planner && wpc up -d --no-build walk-planner
```

## 4. Compose

### 4.1 Standalone

```bash
wpc() { docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml "$@"; }
wpc up -d --no-build walk-planner osrm-foot      # or: wpc --profile cache up -d … to add redis
wpc ps
```

The same `walk.env` feeds both the `${…}` interpolation (`--env-file`) and the container environment
(`env_file:`). If you forget `--env-file`, compose stops with "set WALK_BUNDLE_ID in walk.env and pass it
with --env-file". The compose project is `sloco-walk`, with the network `sloco-walk_default`.

The `env_file:` path comes from `WALK_ENV_FILE` (default `/opt/sloco-data/walk/walk.env`). When `walk.env` is
somewhere else, put `WALK_ENV_FILE=<absolute path of walk.env>` into the file itself; without it compose stops with
`env file /opt/sloco-data/walk/walk.env not found`. `deploy/env.example` does not list this variable yet.

The precedence rule behind this layout: a key under a service's `environment:` beats the same key from its
`env_file:`. The compose file's `environment:` entries are therefore `${…}` interpolations of `walk.env` itself,
so `walk.env` stays the only place to edit. Keep that in mind when you merge the services into another compose
file (§4.2).

### 4.2 On the backend's network

The gateway must reach `http://walk-planner:8000`. Choose one of two ways.

1. **Keep this compose file, and attach `walk-planner` to the backend's network.** Write an override file
   next to `walk.env`, outside the code folder so that upgrades keep it, for example
   `/opt/sloco-data/walk/docker-compose.override.yml`:

   ```yaml
   services:
     walk-planner:
       networks: [default, backend]
   networks:
     backend:
       external: true
       name: <backend network, see `docker network ls`, e.g. backend_sloco_default>
   ```

   Then add `-f /opt/sloco-data/walk/docker-compose.override.yml` to `wpc`, after the main file. The
   service name `walk-planner` resolves on both networks. Everything else in this runbook stays as written.
2. **Merge into `backend_sloco`'s compose.** The services join the backend's default network. In the backend's
   compose file, `${…}` values are filled from the **backend's** environment (its `.env` or `--env-file`), not from
   `walk.env`. So if you copy the `walk-planner` service as it is, its `environment:` block overrides `walk.env`:
   `WALK_ROUTER_URL` becomes `http://osrm-foot:5000` although `walk.env` says empty, `PHOTO_BASE_URL` becomes
   empty, and without `WALK_BUNDLE_ID` in the backend's environment compose stops with "set WALK_BUNDLE_ID in
   walk.env and pass it with --env-file". Do not follow the compose header's "keep this walk.env and list it
   under env_file" either: that is the same trap. Merge like this instead (tried on 2026-10-03 with a test
   compose file; the gateway container reached `http://walk-planner:8000`):
   - Copy the `x-logging` anchor, the `walk-planner` service and the `osrm-foot` service.
   - In `walk-planner`: replace `build:` and `image:` with the pinned image (`sloco-walk-planner:1.0.0` or your
     registry's); keep `env_file:` with the absolute path of `walk.env`; **delete the whole `environment:`
     block**; write literal values where `${…}` stood (host paths, port, CPU and memory limits).
   - In `osrm-foot`: replace `${OSRM_HOST_DIR:-/opt/osrm}`, `${OSRM_CPUS:-1}` and `${OSRM_MEM_LIMIT:-2g}`
     with literal values. It has no `environment:` block.
   - Put the values of the deleted block into `walk.env`, which is now read only by the container:

     ```bash
     # replaces WALK_BUNDLE_ID: switching the data = edit this line + up -d
     WALK_BUNDLE_DIR=/bundles/bucharest-20261002-d68a311e
     # must be written out: unset now means no OSRM at all
     WALK_ROUTER_URL=http://osrm-foot:5000
     # empty, or https://<host>/walk-media
     PHOTO_BASE_URL=
     ENVIRONMENT=production
     LOG_LEVEL=INFO
     WEB_CONCURRENCY=2
     ```

     For the acceptance run (§6 step 9) set `WALK_ROUTER_URL=` empty, as in standalone mode. `WALK_BUNDLE_ID`,
     `WALK_PLANNER_TAG` and the other compose-level variables of §5 have no effect any more.
   - Use the backend's compose command wherever this runbook says `wpc`, and pass the backend's compose file
     (and its env file, if it uses one) to `prepare_osrm.sh --compose-file … [--compose-env …]`; keep the
     service name `osrm-foot` (or pass `--service`).
   - Optionally use the backend's Redis for the leg cache, on a database of its own:
     `WALK_ROUTE_CACHE_REDIS_URL=redis://<redis>:6379/5`. The leg cache needs at most about 256 MB.

Either way: set `WALK_PLANNER_URL=http://walk-planner:8000` on the gateway, with the timeouts of
[`INTEGRATION.md`](INTEGRATION.md) §3.5. Never publish `osrm-foot` or `redis` ports: neither has
authentication. Drop the `127.0.0.1:18600` mapping only if you have another way to run the checks below.
To check what a container really gets, compare `/v1/meta` (`routing.chain`, `settings.photo_base_url`,
`settings.bundle_dir`) with `walk.env`; `docker compose config` shows it too, but prints secrets.

## 5. Environment variables

Everything goes into `/opt/sloco-data/walk/walk.env` (template: `deploy/env.example`; `install -m 600`).
Never pass secrets on a command line, and never paste the output of `docker compose config`: it prints
them.

**Compose level** (interpolation only; the service does not read these):

| Variable | Default | Meaning | Secret |
|---|---|---|---|
| `WALK_BUNDLE_ID` | required | The bundle directory under the bundles dir (= the manifest's `bundle_id`). Compose sets `WALK_BUNDLE_DIR=/bundles/<id>`. Switching or rolling back data = change it + `up -d`. | no |
| `WALK_PLANNER_IMAGE` | `sloco-walk-planner` | image repository, e.g. `ghcr.io/<org>/sloco-walk-planner` | no |
| `WALK_PLANNER_TAG` | `local` | image tag: the release version (§3) | no |
| `WALK_PLANNER_HOST_PORT` | `18600` | loopback port on the host for checks | no |
| `WALK_BUNDLES_HOST_DIR` | `/opt/sloco-data/walk/bundles` | host dir of the bundles | no |
| `OSRM_HOST_DIR` | `/opt/osrm` | OSRM data root (`current` is mounted) | no |
| `WALK_PLANNER_CPUS` / `WALK_PLANNER_MEM_LIMIT` | `2` / `3g` | Limits of `walk-planner`. Memory: about 1.3 GB per worker in the worst case, plus the supervisor and headroom. | no |
| `OSRM_CPUS` / `OSRM_MEM_LIMIT` | `1` / `2g` | limits of `osrm-foot` | no |
| `GIT_SHA` | `unknown` | build argument: the image label and `WALK_GIT_SHA` (§3) | no |
| `WALK_ENV_FILE` | `/opt/sloco-data/walk/walk.env` | path of the `env_file`; set it inside `walk.env` when the file lives elsewhere (§4.1); listed (commented) at the end of `env.example` | no |

These variables work only with the standalone compose file. In a merged compose file (§4.2, merge) the
service reads only `walk.env`, and `WALK_BUNDLE_DIR` replaces `WALK_BUNDLE_ID`.

**Service** (`walk_planner/service/settings.py`; an empty value counts as unset). The service reads only its
process environment. It never reads a `.env` file, not even in a local `serve`: compose's `env_file:` is what
turns `walk.env` into environment variables.

| Variable | Default | Meaning | Secret |
|---|---|---|---|
| `WALK_BUNDLE_DIR` | set by the standalone compose file from `WALK_BUNDLE_ID`; written in `walk.env` when merged (§4.2) | One bundle dir, `a,b`, or a root of bundles (then the newest bundle per city by `built_at`). Required. | no |
| `PHOTO_BASE_URL` | empty | `https://…` or an absolute path such as `/walk-media`. A photo `url` is base + `/` + key; empty means `url: null`. Any other value stops the start-up. | no |
| `WALK_DEFAULT_LANG` | `ru` | `ru` or `en`: language of texts when the request does not choose | no |
| `WALK_GEOMETRY_DEFAULT` | `geojson` | `geojson` or `polyline6`, when the request does not choose | no |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | no |
| `ENVIRONMENT` | `production` | echoed by `/v1/meta`. The default applies everywhere, also to a local `serve`, so set it in every non-production environment | no |
| `WEB_CONCURRENCY` | `2` (image) | uvicorn worker processes. Give each one a core and about 1.3 GB (§12), and divide the ORS limits by it. | no |
| `WALK_MAX_CONCURRENT_PLANS` | `2` | Load guard, **per worker**: plan, schedule and insert requests running at once. One more gets 503 `busy` with `Retry-After: 2` at once. Range 1–64. | no |
| `WALK_VERIFY_BUNDLE` | `true` | Check size and sha256 of every bundle file at start-up; a mismatch stops the start-up. `false` only for fast local restarts. | no |
| `WALK_STARTUP_SMOKE_PLAN` | `true` | Plan one default walk per city at start-up (about 0.4 s; no network) | no |
| `WALK_MAX_BODY_BYTES` | `1048576` | Bodies above this get 413. Range 1024–67108864. A plan request with 500 favourites is about 15 KB; an edit with 150 stops about 25 KB. | no |
| `WALK_GIT_SHA` | from the image | echoed by `/v1/meta` | no |

**Routing** (`walk_planner/routing.py`, read per request; details in [`ROUTING.md`](ROUTING.md)):

| Variable | Default | Meaning | Secret |
|---|---|---|---|
| `WALK_ROUTER_URL` | standalone compose: `http://osrm-foot:5000`; elsewhere (a plain `docker run`, a local `serve`, a merged compose file per §4.2): unset, which means no OSRM | OSRM base URL. **With the standalone compose file, set it empty in `walk.env` to disable OSRM**; removing the line enables it. | only if it embeds credentials |
| `WALK_ROUTER_PROFILE` | `foot` | OSRM profile in the URL path | no |
| `WALK_ROUTER_PROBE_S` | `60` | seconds between background OSRM health/`data_version` probes; `0` = off | no |
| `WALK_ROUTING_DEADLINE_S` | `6` | routing time budget of one request, shared by all its variants | no |
| `ORS_API_KEY` | empty | OpenRouteService key for the fallback when OSRM fails. Use a production key of its own. Empty = no ORS. | **yes** |
| `ORS_BASE_URL` | `https://api.heigit.org/openrouteservice` | ORS host (`api.openrouteservice.org` is being shut down) | no |
| `ORS_MAX_PER_MIN` / `ORS_MAX_PER_DAY` | code `35` / `1800`; `env.example` `17` / `900` | Local limits **per worker**. The free plan allows 40/min and 2000/day per key, so keep `workers × limit` below that. | no |
| `WALK_ROUTE_CACHE_REDIS_URL` | unset | Redis L2 leg cache shared by all workers, e.g. `redis://redis:6379/5`. Unset = an in-process LRU per worker. | **yes**, if it holds a password |

**uvicorn** (image defaults; any CLI option works as `UVICORN_<OPTION>`):

| Variable | Default | Meaning |
|---|---|---|
| `UVICORN_ACCESS_LOG` | `false` | The service writes its own JSON access line. uvicorn's would print query strings, which hold coordinates. |
| `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN` | `20` | seconds for in-flight requests on `docker stop` (compose `stop_grace_period` is 30 s) |
| `UVICORN_TIMEOUT_KEEP_ALIVE` | `65` | longer than the gateway's keep-alive, so pooled sockets are not reset |
| `UVICORN_LIMIT_MAX_REQUESTS` / `_JITTER` | unset (suggested `20000` / `2000`) | recycle a worker after N requests if RSS creeps up (needs 2 or more workers) |

Fixed in the image, leave them alone: `OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS` and `MKL_NUM_THREADS` = 1
(no BLAS oversubscription), `MALLOC_ARENA_MAX=2`, `PYTHONUNBUFFERED=1`, `PYTHONDONTWRITEBYTECODE=1`.

Script variables, not in `walk.env`:
- `sync_bundle.sh`: `SLOCO_SSH` (required, `user@host`), `SSH_OPTS`, `PYTHON`, `SLOCO_PHOTOS_SRC`,
  `SLOCO_PHOTOS_DEST`;
- `prepare_osrm.sh`: `GEOFABRIK_URL`, `OSMIUM_BASE_IMAGE`, `OSMIUM_IMAGE`.

## 6. First deploy, step by step

The flow: start the planner **without street routing and photos**, prove it with the golden acceptance,
then turn routing and photos on. The golden outputs were made with the straight-line estimate and
`url: null` photos.

1. **Recon.**

   ```bash
   docker version; docker compose version     # Compose >= 2.24
   free -h; df -h /opt; nproc
   ss -tlnp | grep -E ':18600|:5000' || true  # ports in use?
   docker ps; docker network ls               # the backend's containers and network
   ```

2. **Directories** (once, as the deploy user):

   ```bash
   sudo install -d -m 755 -o "$USER" /opt/sloco-data/walk /opt/sloco-data/walk/bundles /opt/osrm /opt/walk-planner
   ```

3. **Code.** Check the release archive against the sha256 of [HANDOFF.md §1.1](HANDOFF.md#11-release-identity),
   unpack it and work from its folder:

   ```bash
   sha256sum -c walk-planner-1.0.0.tar.gz.sha256
   tar xzf walk-planner-1.0.0.tar.gz -C /opt/walk-planner/                 # -> walk-planner-1.0.0/
   ln -sfn /opt/walk-planner/walk-planner-1.0.0 /opt/walk-planner/current
   cd /opt/walk-planner/current
   ```

   Alternatively use the vendored `backend_sloco/services/walk-planner` folder ([`RELEASE.md`](RELEASE.md)
   §7).

4. **Image.** Build it (§3), or pull it from the registry.

5. **Bundle.** Either:

   - from the research machine, with SSH to the server:

     ```bash
     SLOCO_SSH=deploy@host deploy/sync_bundle.sh --validate <bundle dir>
     ```

   - or from the bundle archive of the release ([HANDOFF.md §1.1](HANDOFF.md#11-release-identity)), on the
     server:

     ```bash
     cd /opt/sloco-data/walk/bundles
     sha256sum -c bucharest-20261002-d68a311e.tar.gz.sha256
     mkdir -p .incoming && tar xzf bucharest-20261002-d68a311e.tar.gz -C .incoming
     docker run --rm --network none -v "$PWD/.incoming:/b:ro" sloco-walk-planner:1.0.0 \
       python -m walk_planner bundle validate /b/bucharest-20261002-d68a311e     # every check [ok]
     find .incoming -type d -exec chmod 755 {} + && find .incoming -type f -exec chmod 444 {} +
     mv -T .incoming/bucharest-20261002-d68a311e bucharest-20261002-d68a311e && rmdir .incoming
     cd -
     ```

     `mv -T` is GNU coreutils (Linux). It refuses to move into an existing directory, which keeps a bundle from
     being nested inside an old copy. macOS's `mv` has no `-T`; there, check that the target does not exist and
     use plain `mv`.

6. **`walk.env`.**

   ```bash
   install -m 600 deploy/env.example /opt/sloco-data/walk/walk.env
   ```

   Edit it:
   - `WALK_BUNDLE_ID=bucharest-20261002-d68a311e`
   - `WALK_PLANNER_TAG=1.0.0`
   - `GIT_SHA=…`
   - `ENVIRONMENT=production`
   - for the acceptance:
     - `WALK_ROUTER_URL=` (empty: no OSRM; removing the line would enable `osrm-foot`);
     - `ORS_API_KEY=` (empty);
     - `PHOTO_BASE_URL=` (empty).

   If `walk.env` is not at this path, also set `WALK_ENV_FILE` (§4.1). Merged into the backend's compose, the
   file takes the keys of §4.2 instead.

7. **Start the planner only.**

   ```bash
   wpc() { docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml "$@"; }
   wpc up -d --no-build walk-planner
   ```

   An immediate exit with code 78 means the bundle is missing or broken (§15).

8. **Health and meta.**

   ```bash
   wpc ps                                                          # "(healthy)" within the 60 s start period
   curl -fsS http://127.0.0.1:18600/v1/health/ready                # {"status":"ready","bundles":["bucharest-20261002-d68a311e"]}
   curl -fsS http://127.0.0.1:18600/v1/meta | python3 -m json.tool
   ```

   In `/v1/meta` check:
   - `version` = `1.0.0` and `git_sha`;
   - `bundles[0].bundle_id`;
   - `bundles[0].interest.loaded` = `true` (personalisation available);
   - `routing.chain` = `["estimate"]`;
   - `settings.photo_base_url` = `null`;
   - `load.max_concurrent` = 2.

   The service answered `ready` 5.6 s after `up -d` (*measured*, 2 workers, Bucharest bundle). `wpc ps` shows
   `(healthy)` only after the image's first health check, about 30 s after start (§10).

9. **Acceptance: golden replay.** The image holds the package but not the golden files, so mount them:

   ```bash
   docker run --rm --network host -v "$PWD/golden:/golden:ro" sloco-walk-planner:1.0.0 \
     python -m walk_planner golden run --url http://127.0.0.1:18600 \
     --scenarios /golden/scenarios.json --expected-root /golden/expected
   ```

   - The last line must read `26 scenarios: 26 pass`, with exit code 0. That took 22.5 s (*measured*).
   - It replays every recorded call (26 plans and every `/schedule` and `/insert` call of the edit chains)
     with `X-Request-Id: golden-<scenario>-0` (the plan) or `golden-<scenario>-<chain><step>` (an edit, e.g.
     `golden-S01_default-A0`). Numbers are compared within 1e-6.
   - Exit 1 = a difference. If it also printed a WARNING about street routing or `PHOTO_BASE_URL`, routing
     or photos were not disabled. The warning says "restart the service without WALK_ROUTER_URL": with this
     compose file that means `WALK_ROUTER_URL=` set **empty** in `walk.env` (unset means `osrm-foot`), then
     `wpc up -d --no-build walk-planner`.
   - A WARNING with exit 0 = routing is configured, but no router answered and every leg fell back to the
     estimate (for example `osrm-foot` is not running yet). The planner is accepted; street routing is not
     working yet (step 12 checks it).
   - Exit 2 = the service is not reachable or not ready.
   - On Docker Desktop (no host network) drop `--network host` and use `--url http://host.docker.internal:18600`.

10. **OSRM dataset** (about 2 minutes for the Bucharest clip; *measured* 116 s including the 321 MB
    download):

    ```bash
    deploy/osrm/prepare_osrm.sh --bbox 25.80,44.20,26.40,44.70 \
      --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env
    ls -l /opt/osrm/current && wpc ps osrm-foot          # healthy
    ```

    - The script builds as you (no sudo), makes the dataset world-readable, starts a canary hardened like
      compose, switches `current` and recreates `osrm-foot`.
    - Without `--bbox` it builds all of Romania: any Romanian city works, at the cost of more RAM. With the
      clip, routes cannot leave the box: points outside get NoSegment and fall back to the estimate.

11. **Photos.** Ship and serve them (§9) before you set `PHOTO_BASE_URL`.

12. **Turn on routing and photos.** In `walk.env`:
    - delete the `WALK_ROUTER_URL=` line (unset = `http://osrm-foot:5000`; merged into the backend's compose,
      write `WALK_ROUTER_URL=http://osrm-foot:5000` instead, §4.2);
    - set `ORS_API_KEY=<key>` (optional fallback);
    - set `PHOTO_BASE_URL=https://<host>/walk-media`.

    Then recreate the container; environment changes need `up -d`, a `restart` does not re-read them:

    ```bash
    wpc up -d --no-build walk-planner
    curl -fsS -X POST 'http://127.0.0.1:18600/v1/walks/plan?geometry=polyline6' -H 'Content-Type: application/json' \
      -d '{"city":"Bucharest","date":"2026-10-10","start":"city_center"}' \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["versions"]["routing"], [v["summary"]["routing"] for v in d["variants"]])'
    # expected: ok <dataset id, e.g. romania-clip-261001> ['streets', 'streets', 'streets']
    ```

    `/v1/meta`:
    - `routing.chain` = `["osrm", "ors", "estimate"]`, or `["osrm", "estimate"]` without a key;
    - `routing.routers[0].breaker.state` = `closed`;
    - `settings.photo_base_url` is set.

    `golden run --url` now reports differences by design; repeat it only on an acceptance container (§7.1).

13. **Gateway.** Attach the networks (§4.2), set `WALK_PLANNER_URL`, and work through the gateway part of
    [`INTEGRATION.md`](INTEGRATION.md) §8.

## 7. Routine operations

### 7.1 Switch to a new bundle

Bundles are immutable directories named by `bundle_id`. A switch = a new directory + `WALK_BUNDLE_ID` +
`up -d`.

1. Ship it (§6 step 5). The old bundle stays.
2. Run the **acceptance on a temporary container**, so that production keeps its routing. With no
   `walk.env` there is no routing and no photo URL, which is exactly what the golden needs. The expected
   set `golden/expected/<new bundle_id>/` comes with the release that ships the bundle.

   ```bash
   docker run -d --name walk-accept --read-only --tmpfs /tmp:size=64m --cap-drop ALL \
     --security-opt no-new-privileges -p 127.0.0.1:18601:8000 \
     -v /opt/sloco-data/walk/bundles:/bundles:ro -e WALK_BUNDLE_DIR=/bundles/<new bundle_id> -e WEB_CONCURRENCY=1 \
     sloco-walk-planner:<version>
   docker run --rm --network host -v "$PWD/golden:/golden:ro" sloco-walk-planner:<version> \
     python -m walk_planner golden run --url http://127.0.0.1:18601 \
     --scenarios /golden/scenarios.json --expected-root /golden/expected --wait 120
   docker rm -f walk-accept
   ```

   It takes about 30 s and about 0.5 GB of extra RAM (*measured*, one worker; it can peak at about
   1.3 GB). On Docker Desktop, which has no host network, use
   `--url http://host.docker.internal:18601` and drop `--network host`.
3. Set `WALK_BUNDLE_ID=<new bundle_id>` in `walk.env`, then run `wpc up -d --no-build walk-planner`.
4. Check that `/v1/meta` shows the new `bundles[0].bundle_id`.

Effects:
- The container restarts. It refuses connections for about 5–10 s; the gateway retries once
  ([`INTEGRATION.md`](INTEGRATION.md) §3.5). Switch at a quiet hour.
- Clients that hold plans from the old bundle get 409 `catalog_changed` on an edit when one of their stops
  no longer exists, or 422 `place_closed_forever` when it became permanently closed. Other edits work on
  the new data. A short spike of these right after a switch is normal.
- The gateway's config cache must refresh (`versions.catalog` changes).
- Keep about 3 bundles. Delete older ones by hand: `rm -rf /opt/sloco-data/walk/bundles/<old id>`.

### 7.2 Roll back

| What | How |
|---|---|
| The image | Previous `WALK_PLANNER_TAG` in `walk.env`, then `wpc up -d --no-build walk-planner`. The old image must still exist locally or in the registry. |
| The bundle | Previous `WALK_BUNDLE_ID`, then `wpc up -d --no-build walk-planner` |
| Both (a release changed both) | Both variables, one `up -d` |
| OSRM data | `deploy/osrm/prepare_osrm.sh --rollback --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env`. It points `current` back to `previous` and marks the abandoned dataset `ROLLED_BACK`, so a cron run will not switch to it again (override: `--force`). |

After a rollback: `/v1/meta` (version, bundle id, `routing.dataset`) and one smoke plan (§6 step 12).

### 7.3 Refresh the OSRM map (monthly)

The script is idempotent. When Geofabrik's md5 has not changed it prints "up to date" and exits 0. Run it
from cron as the deploy user, with stable paths:

```cron
30 3 2 * * cd /opt/walk-planner/current && deploy/osrm/prepare_osrm.sh --bbox 25.80,44.20,26.40,44.70 --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env >> /opt/osrm/cron.log 2>&1
```

On new data it builds, runs the canary, switches and recreates `osrm-foot`. For those few seconds the
planner falls back to ORS or the estimate, and says so in the responses. If the new dataset is not healthy,
the script switches back by itself (exit 7). Afterwards:
- `versions.routing` and `/v1/meta` `routing.dataset` show the new id, such as `romania-clip-261102`;
- the leg cache starts a fresh namespace, so the first requests miss it;
- the script keeps `current`, `previous` and 2 older datasets.

### 7.4 Rotate the ORS key

1. Create a new key in the openrouteservice (HeiGIT) dashboard, separate from the research dashboard's key.
2. Edit `walk.env`: `ORS_API_KEY=<new key>`.
3. `wpc up -d --no-build walk-planner`: environment changes need a recreate.
4. Check `/v1/meta`: `routing.routers[]` with `name` `ors` shows `key_configured: true` and
   `breaker.state: "closed"`. ORS is called only when OSRM fails, so `quota` may stay empty.
5. Revoke the old key.

The limits are per worker (`ORS_MAX_PER_MIN=17`, `ORS_MAX_PER_DAY=900` with 2 workers), and the key's
plan allows 40/min and 2000/day.

### 7.5 Other routine tasks

- **Capacity:** change `WEB_CONCURRENCY`, `WALK_PLANNER_CPUS`, `WALK_PLANNER_MEM_LIMIT` (and the ORS limits),
  then `up -d` (§12).
- **Logs:** `wpc logs -f --since 10m walk-planner`. Rotation: json-file, 10 MB × 5 per container.
- **Upgrading to a new release:** [`RELEASE.md`](RELEASE.md) §7.
- **Disk:** old OSRM datasets are pruned by the script; old bundles and images by hand. Keep the previous
  release's image.

## 8. Upgrading

See [`RELEASE.md`](RELEASE.md) §7: new folder or tag → build → bundle (if new) → acceptance container →
switch → check → keep the rollback ready.

## 9. Photos

- **What:** card photos are files named by key, `photos_cid/<cid>/<NN>_<label>.jpg`. For the full
  Bucharest catalog that is about 33 GB, 81,777 files, 12,502 places; the bundle references 78,620 of them
  (31.4 GB, at most 10 per place).
  The server already held the food and things-to-do part (11,483 places, 20 GB) on 2026-08-01. The walk
  catalog adds sights and shopping.
- **Ship** them from the research machine; it is incremental, compares sizes only, and runs `chmod a+rX`:

  ```bash
  SLOCO_SSH=deploy@host deploy/sync_bundle.sh --photos [--dry-run]
  ```

- **Check** that every key of the bundle exists on the server:

  ```bash
  docker run --rm --network none -v /opt/sloco-data/walk/bundles:/bundles:ro \
    -v /opt/sloco-data/visual_photo_profiles/photos_cid:/photos_cid:ro sloco-walk-planner:1.0.0 \
    python -m walk_planner bundle validate /bundles/<bundle_id> --photos-root /photos_cid
  # [ok] photo_files — … keys=78620, existing=78620, missing=0
  ```

- **Serve** them with the host's nginx: paste the `location ^~ /walk-media/` block of
  `deploy/photos.nginx.conf.example` into the HTTPS vhost, then `nginx -t && systemctl reload nginx`. It
  serves only `photos_cid/<digits>/<NN>_<label>.jpg` (anything else is 404), GET and HEAD only, with a
  one-week cache plus revalidation.

  ```bash
  curl -sI https://<host>/walk-media/photos_cid/<cid>/00_all.jpg     # 200, image/jpeg, Cache-Control
  curl -sI https://<host>/walk-media/photos_cid/                     # 404 (no listings)
  ```

- **Enable** them: `PHOTO_BASE_URL=https://<host>/walk-media` in `walk.env`, then `up -d`. Without it, cards
  carry keys with `url: null`, and the gateway may build URLs itself
  ([`INTEGRATION.md`](INTEGRATION.md) §3.4).
- **Before launch:** originals average about 0.4 MB and reach about 1 MB, while a phone card needs 50–150 KB.
  Generate resized variants (for example WebP 480 px and 1080 px) or put a resizing proxy behind the prefix;
  keep the key format. These are third-party photos: if they must not be public, protect the prefix (nginx
  `secure_link` with URLs signed by the gateway).

## 10. Health and readiness

| Probe | Semantics |
|---|---|
| `GET /v1/health/live` | 200 `{"status":"alive"}` whenever the process answers. Checks no dependency. |
| `GET /v1/health/ready` | 200 `{"status":"ready","bundles":[…]}` once every bundle is loaded, verified (sha256), warmed and smoke-planned; otherwise 503 `not_ready` with `Retry-After: 5`. uvicorn opens the port only after start-up, so during a start or restart clients mostly see a refused connection rather than a 503. |
| Docker HEALTHCHECK (image) | `ready` every 30 s, timeout 5 s, start period 60 s, 3 retries. The first check runs about 30 s after start, so the container shows `healthy` only then, although the service is ready after 5–6 s. A gateway that waits with `depends_on: {walk-planner: {condition: service_healthy}}` waits those 30 s; probe `/v1/health/ready` instead if that matters |
| `osrm-foot` healthcheck | `GET /nearest/v1/foot/26.1025,44.4355` must say `"Ok"`, every 30 s |

- **Street routing is not part of readiness.** The service keeps answering with estimates, and flags
  them. Watch routing through `/v1/meta` and the logs (§11).
- **Exit codes:**
  - **78** (entrypoint): `WALK_BUNDLE_DIR` unset or a bundle missing or corrupt.
  - **3**: a fatal start-up error with 1 worker.
  - **0**: a fatal start-up error with 2 or more workers (uvicorn's behaviour), for example invalid
    settings, two bundles for one city, or a failing catalog load or smoke plan. Find the `startup_failed`
    or `invalid_settings` line in the log.
- **Restart loops:** `restart: unless-stopped` restarts a failing container with back-off, so watch the
  restart count (`docker inspect -f '{{.RestartCount}}' sloco-walk-walk-planner-1`).
- **`/v1/meta` is per worker.** Its routing counters, leg cache and `load` block describe the worker that
  answered, and two calls can hit different workers. `routing.dataset` stays `null` on a worker that has
  not routed or probed yet.

## 11. Logs, metrics and alerts

Every line on stdout is one JSON object, for the service, the package and uvicorn alike. Common fields:

| Field | Meaning |
|---|---|
| `ts` | UTC time with milliseconds |
| `level`, `logger`, `msg`, `pid` | standard fields |
| `request_id` | inside a request |
| `event` | the event name |
| `exc_type`, `exc_message`, `stack` | on errors |

**Access line**: one per request, logger `walk_planner.service.access`, `event: "request"`.

- Level: health checks answered with 200 are logged at DEBUG, 5xx at WARNING, everything else at INFO.
- Always: `method`, `path`, `status` (499 = the client went away), `duration_ms`, `bytes_out`. On an error:
  `error` (the error code), and for a request-format `validation_error` also `invalid` (the failing fields).
- `endpoint` and the per-endpoint fields below appear once the handler has accepted the request. Lines of a
  404 `not_found`, a request-format 422, a 503 `busy` / `not_ready` or an `unknown_city` have none of them.

| Endpoint | Fields |
|---|---|
| plan | `city`, `bundle_id`, `lang`, `geometry`, `plan_status`, `shape`, `style`, `window_min`, `slots`, `must_visit`, `variants_requested`, `variants`, `start_kind` (`city_center`, `point` or `place`), `start` (rounded to 2 decimals), `personalization`, `messages` (codes) |
| plan, schedule, insert | `routing`, `timings`, `stops[]`, `over_budget[]`, `dropped_slots[]`, `routing_quality[]` (one per variant) |
| schedule, insert | `stops_in`; insert also `inserted_index` |
| search | `q_len` (never the text), `near`, `limit`, `include_closed`, `results` |
| place | `found` |

The nested blocks:
- `personalization`: `{favourites, want_to_go, ids_digest, mode, used, ignored, profiles, strength, error}`.
  Ids appear only as a count and a salted digest.
- `routing`: `{provider, chain, spent_ms, legs{streets, estimate}, failed[], events, data_version}`, plus
  `event_log` (at most 10 router steps) only when a router failed or legs fell back to the estimate.
- `timings`: `{interest_ms, candidates_ms, solver_ms, router_ms, render_ms, plan_ms | edit_ms}`.

**Lifecycle events** (logger `walk_planner.service`):

| Event | Level | Fields / meaning |
|---|---|---|
| `bundle_loaded` | INFO | `{bundle_id, city, places, taste, verified, ms}` |
| `bundle_ready` | INFO | `{warm_ms, smoke_plan}` |
| `ready` | INFO | `{bundles, cities, startup_ms, version, git_sha, environment}` |
| `stopping` | INFO | shutting down |
| `smoke_plan_empty` | WARNING | the start-up plan found nothing |
| `startup_failed`, `invalid_settings`, `stopping_supervisor` | CRITICAL | a fatal start-up error |
| `unhandled_exception` | ERROR | with the stack; the client got 500 `internal_error` with the request id |

Routing (logger `walk_planner.routing`):
- WARNING `router osrm failed: timeout …` (or `ors`, with the kind: `connect`, `http_5xx`, `quota`, `auth`,
  `config` …);
- INFO `routing fell back to the straight-line estimate for N of M legs`.

The service has no `/metrics` endpoint in v1. Derive the signals from the access log (Loki, Vector, etc.)
and from polling `/v1/meta`:

| Signal | From | Alert when |
|---|---|---|
| Readiness | `GET /v1/health/ready` from the host or the gateway; the container's health and restart count | not 200 for 2 min; the restart count grows |
| Server errors | `status: 500` with `error: internal_error` (503 `busy` / `not_ready` are load, not errors) | more than 0 in 5 min (each has a `request_id`, and the `unhandled_exception` line has the stack) |
| Load shedding | `error: busy` (503) per minute | sustained for more than 10 min: add capacity (§12) |
| Plan latency | `duration_ms` of `endpoint: plan` (typical 0.3–0.5 s; 24-hour plans 3–4 s, about 6.5 s when two share a worker; plus street routing; [SPEC.md §10](SPEC.md#10-performance-and-capacity)) | p95 above 8 s over 15 min |
| Edit and search latency | `endpoint` schedule, insert, search (typical 3–60 ms) | p95 above 300 ms |
| Street routing | `routing_quality` containing `estimate` or `mixed` while OSRM is configured; `/v1/meta` `routing.routers[].breaker.state == "open"`; ORS `quota.remaining` | more than 5 % of plans in 15 min; a breaker open for more than 10 min |
| Personalisation | `personalization.error` not null, or `mode: popularity` while `favourites > 0` on every request | any sustained |
| Client bugs | `error: validation_error` rate, with the `invalid` fields | a spike after an app or gateway release |
| Data drift | `/v1/meta` `bundles[].bundle_id` against the expected `WALK_BUNDLE_ID`; `versions.routing` | a mismatch |
| Memory | `docker stats` of `walk-planner` against its limit | above 85 % of the limit |

## 12. Capacity and scaling

| | Value |
|---|---|
| Unit of work | one uvicorn worker = one core; it holds the catalog and taste model (about 0.5 GB RSS idle) |
| Heavy requests at once | `WEB_CONCURRENCY × WALK_MAX_CONCURRENT_PLANS` (2 × 2 = 4 by default). One more gets 503 `busy` at once (no queue); search, place, config, meta and health are never limited. |
| Latency (*measured*, p50, the image under Docker Desktop, one request at a time; [SPEC.md §10](SPEC.md#10-performance-and-capacity) has p90 and a second set-up) | plan 0.43 s (4-hour walk), 0.50 s with favourites, 4.0 s for a 24-hour walk; schedule and insert 3–4 ms (about 53 ms with favourites in the echo); search 13–15 ms; place 0.7 ms; config 0.3 ms |
| Throughput | about 2.3 four-hour plans per second per worker, or about 0.25 twenty-four-hour plans per second per worker |
| Memory under bursts (*measured*, 2 workers, 30 large plans at once, clients retrying on `busy`) | guard 2 (default): 0.64 GB peak per worker, 1.1 GB container, all served in 71 s (p50 34 s), search p50 75 ms meanwhile. Guard 4: 0.73 GB / 1.3 GB, 64 s, search p50 129 ms. No guard (64): 1.12 GB / 2.1 GB, 68 s but p50 56 s, search up to 1.9 s. |

- **Scale up.** Raise `WEB_CONCURRENCY` together with `WALK_PLANNER_CPUS` (one core each) and
  `WALK_PLANNER_MEM_LIMIT` (about 1.3 GB per worker plus 0.4 GB), and divide the ORS limits by the worker
  count. Raise `WALK_MAX_CONCURRENT_PLANS` only together with memory: it trades memory and search latency
  for nothing in total throughput.
- **Scale out.** Run more replicas behind the gateway; the service is stateless, so any replica can serve
  any request, including edits. Share the leg cache with `WALK_ROUTE_CACHE_REDIS_URL`, and divide the ORS
  limits by the total number of workers. With several replicas, drop the fixed host port.
- **RSS creep.** If memory grows over days, set `UVICORN_LIMIT_MAX_REQUESTS=20000` and `_JITTER=2000`.

## 13. Hardening

What the compose file and the image already do:

- **Process:** non-root (uid/gid 10001), read-only root filesystem (only `/tmp`, a 64 MB tmpfs, is
  writable), `cap_drop: [ALL]`, `no-new-privileges`, `init`.
- **Data:** bundles mounted read-only, with long-syntax binds that fail when the host path is missing
  rather than creating an empty directory.
- **Network:** published only on `127.0.0.1:18600`. `osrm-foot` and `redis` are never published, and
  `osrm-foot` runs with the same hardening.
- **Image:** base pinned to a Python patch release; dependencies hash-pinned and installed from wheels
  only; no build tools in the runtime image.
- **Secrets:** only in `walk.env` (mode 600, outside the code folder). `/v1/meta` strips credentials,
  query strings and fragments from every URL; the routing config's repr hides the key; logs carry no
  favourites (a digest only), no precise coordinates (2 decimals) and no search text.

What is up to you:
- keep `walk-planner` unreachable from the internet: the gateway only, since there is no auth;
- protect the photo prefix if needed (§9);
- use a separate ORS key per environment;
- rebuild the image regularly for Debian security fixes.

## 14. CI

`.github/workflows/walk-planner-ci.yml` (research repo) runs on pushes to `main` and on pull requests that
touch `services/walk_planner/**`. It uses no secrets and pushes nothing.

- **tests**: Python 3.12, both lock files with hash checking, `pip check`, the package test suite (offline;
  the real-data tests skip), and `tools/export_openapi.py --check` plus `tools/export_messages.py --check`.
- **image**: builds the image, then checks that:
  - the service imports;
  - the user is 10001:10001;
  - a missing bundle and a corrupted one (with 2 workers) both exit 78;
  - the committed mini bundle is served, hardened like compose, until `/v1/health/ready` is 200;
  - the mini golden calls replay against the running container (`golden run --url`).

To publish images to GHCR, add a job on `walk-planner-v*` tags with `permissions: packages: write` and
`docker/build-push-action`.

The workflow lives in the research repo at `.github/workflows/walk-planner-ci.yml`; a copy ships in the package
as `deploy/ci/walk-planner-ci.yml`. When the folder is vendored into `backend_sloco`, copy it to your
`.github/workflows/` and adjust its `paths` and `working-directory`. Its acceptance step is the mini replay of §16.

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container exits at once with **code 78**; log: "no data bundle at WALK_BUNDLE_DIR…" or "failed validation" | The bundle is not synced or not mounted, `WALK_BUNDLE_ID` is wrong, a copy is half-synced or corrupt, or the files are not readable by uid 10001 | `ls /opt/sloco-data/walk/bundles/$WALK_BUNDLE_ID/manifest.json`; re-sync (§6 step 5); dirs 755, files 444; run the `bundle validate` command from §6 step 5 for the report. |
| Container exits with **code 0** shortly after start (2 or more workers) | A deeper start-up failure, past the entrypoint's bundle check: invalid settings (e.g. `PHOTO_BASE_URL` neither `http(s)://` nor `/…`), two bundles for one city, a failing catalog load, taste warm-up or smoke plan | `wpc logs walk-planner \| grep -E 'startup_failed\|invalid_settings'`; fix the setting or the bundle. `WEB_CONCURRENCY=1` makes the exit code 3. |
| `docker compose` stops with "set WALK_BUNDLE_ID in walk.env and pass it with --env-file" | `--env-file` is missing; or the service was merged into another compose file with its `environment:` block | Use the `wpc` function (§4.1); merged: follow §4.2. |
| `docker compose` stops with `env file /opt/sloco-data/walk/walk.env not found` | `walk.env` is somewhere else | Put `WALK_ENV_FILE=<its path>` into `walk.env` (§4.1). |
| Merged into the backend's compose: `/v1/meta` shows OSRM in `routing.chain` although `walk.env` says `WALK_ROUTER_URL=`, or `settings.photo_base_url` is `null` although `walk.env` sets it | The copied `environment:` block overrides `walk.env` with values from the backend's environment | Delete the block and move its values into `walk.env` (§4.2). |
| The image is tagged `sloco-walk-planner:sha-`; `/v1/meta` `git_sha` is empty | `GIT_SHA` was empty at build time (for example `git rev-parse` outside a checkout) | Rebuild with the commit of the release (§3). |
| `up` fails: bind source path `/opt/osrm/current` does not exist | No OSRM dataset yet | Run `prepare_osrm.sh` (§6 step 10), or start only `walk-planner`. |
| **503 `not_ready`** | The service is starting or stopping | Wait for `Retry-After` (5 s). If it persists, check the logs for `startup_failed`. |
| **503 `busy`** | Every load-guard slot of the worker is taken | Expected under bursts: the gateway retries. If sustained, scale (§12). Do not just raise `WALK_MAX_CONCURRENT_PLANS` without memory. |
| `osrm-foot` exits 1: `File /data/region.osrm.fileIndex mapping failed: … Permission denied` | The dataset files are not world-readable (built by an older script, or copied). osrm-foot runs as root without capabilities and can only read what "other" may read. | `chmod -R a+rX /opt/osrm/datasets/<id>` (or re-run `prepare_osrm.sh`, which repairs it), then `wpc up -d osrm-foot`. |
| **Every segment is `estimate`** (variant message `routing_estimate`) although OSRM is configured | `osrm-foot` is down or unhealthy; `WALK_ROUTER_URL` is wrong (host, path) or still set empty in `walk.env`; the OSRM breaker is open; with `--bbox`, points outside the box (NoSegment) | `/v1/meta` → `routing.chain`, `routers[osrm].breaker`, `last_probe`; `wpc ps osrm-foot`; logs `router osrm failed`; from inside: `docker exec <walk-planner container> python -c "import urllib.request;print(urllib.request.urlopen('http://osrm-foot:5000/nearest/v1/foot/26.1025,44.4355').read()[:80])"`. |
| ORS fallback never used | No key; 401 (bad key → breaker 1 h); 403 (daily quota → until the reset); `config` kind (wrong `ORS_BASE_URL` → 1 h); local limiter exhausted | `/v1/meta` `routers[ors]`: `key_configured`, `breaker`, `limiter`, `quota`; fix the key or URL, `up -d`. |
| **409 `catalog_changed`** right after a bundle switch | Clients edit plans from the old bundle that include a place the new bundle lacks | Expected. The app rebuilds the plan. If it persists, a client keeps sending an old `request` echo. |
| 422 `validation_error` on every plan from the gateway | The gateway sends ids as numbers, `9:00` instead of `09:00`, or wrong field names | Read `params.errors[].loc` and the `invalid` field of the access line. |
| 413 `payload_too_large` | The body is over `WALK_MAX_BODY_BYTES` | The gateway must cap favourites at 500 ([`INTEGRATION.md`](INTEGRATION.md) §3.3). |
| Cards have `url: null` | `PHOTO_BASE_URL` is unset | §9. |
| Photo URLs return 404 | Photos not synced, nginx path or permissions wrong, base URL mismatch | `bundle validate --photos-root` (§9); `curl -sI` a URL; `ls -l` the file. |
| `golden run --url` exits **2** | The service is not reachable or not ready, or the URL is not walk-planner | Check the URL and port; `--network host` (Linux) or `host.docker.internal` (Docker Desktop); `--wait 120`. |
| `golden run --url` exits 1 and prints a WARNING about street routing or `PHOTO_BASE_URL` | Acceptance against a service with routing or photos on | Use the acceptance container (§7.1) or disable both (§6 step 6): `WALK_ROUTER_URL=` set empty, not removed. |
| `golden run --url` prints a WARNING about street routing but exits 0 | Routing is configured, but no router answered, so every leg fell back to the estimate | The planner is accepted. Street routing is broken: check `osrm-foot` (row "Every segment is `estimate`" above). |
| `golden run --url`: "no expected set" for the served bundle | The release's `golden/expected/` has no set for this `bundle_id` | Deploy the bundle the release was accepted on, or get its expected set from the research side ([`RELEASE.md`](RELEASE.md) §5). |
| OOM kill (exit 137) or the memory alert | Too many workers or too high a guard for the limit | Raise `WALK_PLANNER_MEM_LIMIT` (about 1.3 GB per worker), or lower `WALK_MAX_CONCURRENT_PLANS` / `WEB_CONCURRENCY`. |
| `prepare_osrm.sh` exits 5 with "killed (exit 137 …)" | Not enough memory to build | Use `--bbox`, raise `--build-mem`, or build elsewhere and copy `datasets/<id>`, then `chmod -R a+rX`. |
| `prepare_osrm.sh` exits 6 (canary failed) | The new dataset does not route the smoke points | Nothing was switched. Read `/opt/osrm/logs/<run>/`; for other regions pass `--smoke "lon,lat;lon,lat"`. |
| Redis unreachable | The optional L2 cache is down | Harmless: the leg cache keeps working in-process (`/v1/meta` `cache.redis: "unavailable"`). |
| Gateway sees sporadic ECONNRESET | Keep-alive mismatch | The service keeps sockets for 65 s (`UVICORN_TIMEOUT_KEEP_ALIVE`); keep the gateway's idle timeout below that. |

## 16. Local trial with Docker (mini bundle)

The whole stack can be tried on a laptop with Docker Desktop, before any server work: the image, the compose
file, `walk.env`, start-up and the acceptance replay. It uses the committed synthetic mini bundle (a 60-place
city, [`tests/fixtures/mini/`](../tests/fixtures/mini/README.md)), no `/opt` directories and no OSRM. This recipe
was run on 2026-10-03 (Docker Desktop on macOS, arm64).

1. Build the image (§3). Any `GIT_SHA` label will do for a trial, for example `GIT_SHA=local-trial`.
2. Write a `walk.env` outside the package, with **absolute** paths:

   ```bash
   L="$HOME/walk-local"; mkdir -p "$L/osrm"
   cat > "$L/walk.env" <<ENV
   WALK_ENV_FILE=$L/walk.env
   WALK_BUNDLES_HOST_DIR=$PWD/tests/fixtures/mini/bundles
   OSRM_HOST_DIR=$L/osrm
   WALK_BUNDLE_ID=minitown-20261002-17bd5998
   WALK_PLANNER_TAG=1.0.0
   WALK_ROUTER_URL=
   ORS_API_KEY=
   PHOTO_BASE_URL=
   ENVIRONMENT=development
   ENV
   chmod 600 "$L/walk.env"
   ```

   `WALK_ENV_FILE` points compose at this file (§4.1). `OSRM_HOST_DIR` only has to exist, because `osrm-foot` is
   not started. If port 18600 is taken, add `WALK_PLANNER_HOST_PORT=<another port>` and use it below.
3. Start the planner and wait for it:

   ```bash
   wpl() { docker compose --env-file "$L/walk.env" -f deploy/docker-compose.yml "$@"; }
   wpl config --quiet && wpl up -d --no-build walk-planner
   curl -fsS http://127.0.0.1:18600/v1/health/ready      # {"status":"ready","bundles":["minitown-20261002-17bd5998"]}
   ```

   It was ready about 4 s after `up -d`.
4. Replay the mini golden set against it, from the image itself (the CI does the same):

   ```bash
   docker run --rm -v "$PWD/tests/fixtures/mini:/mini:ro" sloco-walk-planner:1.0.0 \
     python -m walk_planner golden run --url http://host.docker.internal:18600 \
     --scenarios /mini/scenarios.json --expected-root /mini/expected --wait 60
   # ... 8 scenarios: 8 pass
   ```

   On Linux use `--network host` and `--url http://127.0.0.1:18600` instead.
5. The real data, if you have the Bucharest bundle: set `WALK_BUNDLES_HOST_DIR` to the directory that holds
   `bucharest-20261002-d68a311e/` and `WALK_BUNDLE_ID=bucharest-20261002-d68a311e`, run `wpl up -d --no-build
   walk-planner` again (ready after about 6 s), and replay the 26 scenarios with `-v "$PWD/golden:/golden:ro"`,
   `--scenarios /golden/scenarios.json --expected-root /golden/expected` (`26 scenarios: 26 pass`, 22.5 s).
6. Clean up: `wpl down`.
