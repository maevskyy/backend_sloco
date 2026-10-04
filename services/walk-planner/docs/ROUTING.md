# Walk Planner street routing: OSRM → ORS → estimate

This document is for the backend developer who runs the `walk-planner` service. It explains how the service turns a
planned route into walking legs along streets, and what happens when routing is unavailable. It also covers how to
run the self-hosted OSRM router, how to configure the ORS cloud fallback, and how to monitor and troubleshoot both.

The code is in [`walk_planner/routing.py`](../walk_planner/routing.py), with tests in
[`tests/test_routing.py`](../tests/test_routing.py) (80 offline tests). The router build script is
[`deploy/osrm/prepare_osrm.sh`](../deploy/osrm/prepare_osrm.sh). Services and variables are defined in
[`deploy/docker-compose.yml`](../deploy/docker-compose.yml) and [`deploy/env.example`](../deploy/env.example).

Related documents: [DATA.md](DATA.md), [API.md](API.md), [DEPLOY.md](DEPLOY.md), [ALGORITHM.md](ALGORITHM.md).

## Contents

1. [Summary](#1-summary)
2. [What routing changes, and what it never changes](#2-what-routing-changes-and-what-it-never-changes)
3. [The chain](#3-the-chain)
4. [Quality flags and UI guidance](#4-quality-flags-and-ui-guidance)
5. [OSRM: the self-hosted primary](#5-osrm-the-self-hosted-primary)
6. [ORS: the cloud fallback](#6-ors-the-cloud-fallback)
7. [Leg cache](#7-leg-cache)
8. [Timeouts, retries, circuit breakers, deadline](#8-timeouts-retries-circuit-breakers-deadline)
9. [Configuration](#9-configuration)
10. [Observability](#10-observability)
11. [Troubleshooting](#11-troubleshooting)
12. [Calibration: estimate versus streets](#12-calibration-estimate-versus-streets)
13. [Known limitations and options](#13-known-limitations-and-options)

---

## 1. Summary

- **Routing never decides the plan.** The planner chooses and orders places with a straight-line estimate:
  `haversine × 1.35 at 4.5 km/h`, offline and deterministic. Street routing is used only *after* that, to draw the
  chosen route and re-time it. An outage therefore never changes which places a user gets.
- **The chain.** For each assembled route the service tries, in order:
  1. the per-leg cache;
  2. **self-hosted OSRM** (foot profile), one GET for all legs;
  3. **ORS cloud** at `api.heigit.org`, one POST per up to 50 points, rate-limited;
  4. the **straight-line estimate**.

  Legs are never requested one by one.
- **Never silent.**
  - Every segment carries `quality` (`streets` | `estimate`) and `provider` (`osrm` | `ors` | `haversine`).
  - Every variant carries `summary.routing` (`streets` | `estimate` | `mixed` | `none`).
  - A variant gets a `routing_estimate` message whenever at least one of its legs is an estimate.
- **Bounded time.**
  - One request has a 6 s routing budget, shared by all of its variants.
  - A router that fails is skipped for the rest of that request.
  - Circuit breakers keep a dead router from costing time on later requests.
- **Defaults.**
  - Nothing configured means estimate only. The golden tests and acceptance replays use that setting.
  - Production uses the compose service `osrm-foot`: OSRM v26.10.0, `foot.lua`, MLD algorithm, built from a
    Geofabrik extract by `prepare_osrm.sh`.
  - ORS is only a fallback and needs a key of its own.
- **Attribution must be shown.**
  - Wherever ORS routes are displayed: **"© openrouteservice.org by HeiGIT | Map data © OpenStreetMap contributors"**.
  - Wherever OSRM routes are displayed: "© OpenStreetMap contributors".

---

## 2. What routing changes, and what it never changes

**Never changed by routing.**
- Which places are chosen, their order, and the number of different variants.
- The candidate pools and the search radius.
- Where `/insert` places a new stop. `best_insertion` also uses the estimate.

The optimizer only calls `RoutingProvider.walk_minutes`, which the chain inherits unchanged. The test
`tests/test_routing.py::test_routing_never_changes_which_places_are_planned` checks this. Variants are de-duplicated
by their place sequence, so routing cannot change de-duplication either.

**Changed by routing.** These are all display and timing values of the chosen route:
- `segments[]`: `walk_min`, `distance_m`, `geometry`, `quality`, `provider`.
- Stop times: `arrival`, `visit_start`, `departure`, `wait_min`.
- The hours re-check at the real arrival times: `hours.status` and the `stop_hours_conflict` messages.
- Totals: `summary.walk_min`, `distance_km`, `total_min`, `finish_at`, `slack_min`, `over_budget`.
- `bbox`, `versions.routing`, and therefore `plan_id`.
- The `routing_estimate` message.

**Example.** The same S01 plan (Bucharest, variant 1, 7 stops), assembled with the estimate and with OSRM. This was
measured on 2026-10-02 against a Bucharest clip of OSRM v26.10.0.

| | Estimate | OSRM |
| --- | --- | --- |
| Stops and order | identical | identical |
| Walking | 19.7 min, 1.48 km | 19.0 min, 1.58 km |
| Finish ("return") | 13:45 | 13:44 |
| One short leg | 100 m, 1.3 min (straight line) | 187 m, 2.2 min (a 19-point street line) |

**Why the planner does not use the router.**
- Plans stay deterministic.
- The golden outputs can be checked offline.
- A router outage cannot change plans.
- The estimate is conservative for the legs the planner actually uses (§12).

Using a router distance matrix in the optimizer is a v2 option (§13).

---

## 3. The chain

```
one assembled route = points [start?, stop 1, ..., stop n, start? (loop)]
route_legs(points)                    -> exactly len(points) - 1 legs, in order; never raises
  1. leg cache   L1 (in-process LRU) -> L2 Redis (optional)       every leg cached -> done, 0 HTTP
  2. OSRM        GET  {WALK_ROUTER_URL}/route/v1/foot/{lon,lat;...}     1 request for all legs
  3. ORS         POST {ORS_BASE_URL}/v2/directions/foot-walking/geojson 1 request per <= 50 points
  4. estimate    haversine x 1.35 at 4.5 km/h, 2-point straight line    cached street legs are kept
```

**How many times the chain runs:**
- `POST /v1/walks/plan` runs it once per assembled variant: 3 by default, up to 5. Duplicate variants are dropped
  after assembly, but by then their legs come from the cache.
- `/schedule` and `/insert` run it once each.

**Rules:**

- **Cache.**
  - If every leg of the route is cached, no HTTP request is made.
  - If only some are cached, the whole route still goes to the router, as one request, never one per leg.
  - If the chain then falls back to the estimate, the cached street legs are kept and only the rest are estimated.
    The plan's quality is then `mixed`.
- **Routers raise; only the chain falls back.** A router either returns exactly `len(points) - 1` legs or raises
  `RoutingError(kind)` (the kinds are listed in §8). The chain logs each step as an event (§10).
- **Unroutable input stops the chain.** `no_segment` (a point more than 1 km from any walkable way) and `no_route`
  (disconnected points) go straight to the estimate. The next router would fail on the same points, and ORS would
  spend quota on it.
- **A failed router is skipped for the rest of the request.** Its timeout is paid once, not once per variant.
- **Deadline.** `WALK_ROUTING_DEADLINE_S` (6 s) is the total time one request may spend waiting on routers, summed
  over its variants:
  - every HTTP attempt is capped by what is left;
  - below 50 ms left, the remaining legs are estimated;
  - planning CPU time between variants is not counted.
- **Scope.**
  - Process-wide: routers, circuit breakers, the ORS limiter, the L1 cache and the counters, shared by the threads
    of one worker.
  - Per request: `make_provider(start=...)` builds a fresh `ChainProvider` with the request's budget, the start
    point, the set of routers that failed and the event list.
  - With nothing configured, `make_provider` returns the plain estimator: no network and no cache.

**Why this order:**

| Step | Why it is there |
| --- | --- |
| Cache | Free. |
| OSRM | Ours: no quota, millisecond latency, coordinates stay on our hosts. |
| ORS | Metered, rate-limited and a third party, so it is used only while OSRM is down. |
| Estimate | Always available, and flagged. |

---

## 4. Quality flags and UI guidance

### 4.1 What the API returns

| Field | Values | Meaning |
| --- | --- | --- |
| `variants[].segments[].quality` | `streets` / `estimate` | Street-router leg, or straight-line estimate |
| `variants[].segments[].provider` | `osrm` / `ors` / `haversine` | Where the leg came from (a cached leg keeps its original provider) |
| `variants[].segments[].geometry` | GeoJSON `LineString`, `[lon, lat]` | With `geometry=polyline6`: `geometry_polyline6` instead (Google encoded polyline, precision 6, **lat,lon inside**; in TypeScript `@mapbox/polyline`'s `toGeoJSON(str, 6)` returns a GeoJSON LineString with `[lon, lat]` coordinates) |
| `variants[].summary.routing` | `streets` / `estimate` / `mixed` / `none` | All legs streets / all estimates / some of each / no segments (empty route) |
| `variants[].messages[]` code `routing_estimate` | params `{segments_estimated, segments_total}`, severity `info` | Present when at least one leg is an estimate. RU: «Пешие отрезки посчитаны по прямой (оценка): N из M — по улицам путь может быть длиннее.» |
| `versions.routing` | OSRM `data_version` (e.g. `romania-clip-261001`) or `null` | The last OSRM dataset this worker heard of, even when this request's legs came from ORS or the estimate (`segments[].provider` says where each leg came from). `null` when OSRM is not configured or has not answered this worker yet |

### 4.2 How the app should show it

| `summary.routing` | Line on the map | Times | Banner |
| --- | --- | --- | --- |
| `streets` | Solid, along the street geometry | As given | none |
| `mixed` | Solid for street segments, **dashed** for estimate segments | **"≈"** on the estimated legs and on every time after the first estimated leg (arrivals, finish, walking total) | The `routing_estimate` text |
| `estimate` | Dashed straight lines | "≈" on all walking times and on the finish | The `routing_estimate` text, or the UX report's «Точный маршрут по улицам сейчас недоступен — время приблизительное» |
| `none` | — | — | The variant has no stops (`route_empty`) |

Notes for the client:

- **Estimate distances are inflated.** The `distance_m` of an estimated leg is the straight line × 1.35, not the
  straight line.
- **Street geometry starts at a snapped point.** A street leg starts and ends at the point OSRM snapped to the
  nearest walkable way. In a sample that point was a median of 12 m and at most 43 m from the place. Draw stop pins
  at `stops[].lat/lon`, not at the ends of the line.
- **Upgrading a stored estimate plan.** Once routing works again, send the variant's unchanged `sequence` to
  `POST /v1/walks/schedule`. The stops and order stay the same, and the times come from the router. The response is
  marked `edited: true`, so the plan-time messages `extras_added` and `slots_dropped` are not repeated.
- **Navigation links are independent.** The Google and Apple Maps links in `navigation` route on their own side and
  do not depend on this chain.
- **Attribution.** The API carries no attribution field; decide from `segments[].provider`:
  - `osrm` or `ors` anywhere: show "© OpenStreetMap contributors" (the OSM data is under ODbL);
  - `ors` anywhere: show the full string "© openrouteservice.org by HeiGIT | Map data © OpenStreetMap contributors";
  - the base map keeps its own attribution.

---

## 5. OSRM: the self-hosted primary

### 5.1 Image and version pin

- **Image:** `ghcr.io/project-osrm/osrm-backend:v26.10.0-debian`, released 2026-10-01, for amd64 and arm64.
  - A plain `v26.10.0` tag does not exist (404).
  - `latest` is the master branch. Never use it.
- **Build and serve with the same version.** OSRM data files must be built by the version that serves them. Change
  the compose `osrm-foot.image` and `prepare_osrm.sh --image` (whose default is the same tag) together, then rebuild
  with `--force`.
- **Older servers.** `overview=by_legs` needs OSRM v26.4.0 or newer. The router detects an older server (a 400 with
  `InvalidQuery`, `InvalidOptions` or `InvalidValue`) and switches to `steps=true`. It gets the same legs with a
  larger payload, and each worker remembers the switch.

### 5.2 The request

```
GET {WALK_ROUTER_URL}/route/v1/foot/{lon,lat;lon,lat;...}
    ?overview=by_legs&geometries=geojson&steps=false&generate_hints=false&radiuses=1000;1000;...
```

- **Response.** `routes[0].legs[i]` gives `duration` (seconds), `distance` (metres) and `geometry` (`[lon, lat]`).
  The adapter checks that there are exactly `len(points) - 1` legs.
- **`radiuses=1000`.** A point more than 1 km from any walkable way returns `NoSegment` instead of snapping somewhere
  absurd.
- **Waypoint limit.** OSRM's default is 500 waypoints. The longest possible plan has 98 stops, and an edited route at
  most 150, plus the start.
- **Profile name.** `WALK_ROUTER_PROFILE` (default `foot`) is only the URL path segment. One `osrm-routed` serves one
  dataset and ignores the name.

### 5.3 Foot profile: stock `/opt/foot.lua`

- **5 km/h on every walkable way type**, including steps, footways, residential streets and primary roads.
- Slower surfaces: gravel, fine gravel and pebblestone ×0.75; mud and sand ×0.5.
- Penalties: +2 s per traffic signal and +2 s per U-turn.
- Ferries at 5 km/h.
- Ways tagged `sidewalk=separate` are closed, so routes use the separately mapped sidewalk.
- Squares are walked around their outline. The experimental `foot_area.lua` would cross them, but it is not used.

Changing the speed means editing the profile and rebuilding. It is not done in v1 because it changes the times
shown (§12, §13).

### 5.4 Resources

| | Bucharest clip (`--bbox 25.80,44.20,26.40,44.70`) | Romania-wide (no `--bbox`) |
| --- | --- | --- |
| Download | Geofabrik `europe/romania-latest.osm.pbf`, 321 MB, 87 s (measured) | same |
| Build | **18–19 s measured**: clip 4–6 s, extract 3–6 s, partition 1–2 s, customize 1 s | est. 5–30 min |
| Build RAM | under 1 GB (est.) | est. 2.5–4 GB peak; the script caps each build container at `--build-mem 6g` |
| Dataset on disk | **107–108 MB measured** | est. 1.5–2.5 GB, plus 0.33 GB for the extract |
| Serving RAM | a few hundred MB (est.) | about 1 GB (est.) |
| Coverage | only the box. Points outside give `NoSegment`, so those legs become estimates | every Romanian city |
| Free disk the script checks for | 1 GB (plus 1.5 GB for a download) | 4 GB (plus 1.5 GB) |

- The Bucharest catalog lies inside the clip box: longitude 25.889–26.312, latitude 44.288–44.586.
- The compose limits for `osrm-foot` are 1 CPU and 2 GB (`OSRM_CPUS`, `OSRM_MEM_LIMIT`). Check `docker stats` on the
  first Romania-wide build; the Romania-wide figures are estimates.

**Measured end to end** (2026-10-02, planner container plus the Bucharest clip):

| Request | Result |
| --- | --- |
| S01 plan, 3 variants | 23 segments, all `streets`; 9.8 ms waiting on the router |
| The same plan again | 0 ms router time: 23 of 23 legs from the in-process cache, and a byte-identical response |
| `/schedule` with a moved stop | 13 ms, 5 of 8 legs cached |
| `/insert` | 15 ms |

### 5.5 `prepare_osrm.sh`

The script builds or refreshes the dataset, tests it, and switches to it. Run it on the server as the **non-root
owner** of `/opt/osrm`, without sudo, from `services/walk_planner`. It needs bash, docker, curl, md5sum and python3;
flock is optional.

```bash
# once
sudo install -d -m 755 -o "$USER" /opt/osrm

# first build (Bucharest clip), switch, recreate osrm-foot, wait until healthy
deploy/osrm/prepare_osrm.sh --bbox 25.80,44.20,26.40,44.70 \
  --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env

# Romania-wide instead: omit --bbox (more RAM and disk, see §5.4)
deploy/osrm/prepare_osrm.sh --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env

# monthly refresh (cron-safe): the same command. It rebuilds only when Geofabrik's md5 changed;
# otherwise it reports "up to date: current = <id>" and exits 0.
```

Until the first dataset exists, start only the planner (`... up -d walk-planner`). It then routes through ORS or the
estimate and says so in every response.

| Option | Meaning |
| --- | --- |
| `--bbox W,S,E,N` | Clip the extract with osmium. The script builds a local `sloco-osmium-tool:trixie` image once (osmium-tool 1.18 from Debian trixie) |
| `--region PATH` | Geofabrik path (default `europe/romania`) |
| `--pbf FILE` | Build from a local `.osm.pbf`: no download and no md5 file; the date is the file's mtime. Use it for merged multi-city extracts (§5.9) |
| `--name NAME` | Dataset name prefix. Default: the region's base name, `<name>-clip` with `--bbox`, the file name with `--pbf` |
| `--base DIR` | Data root (default `/opt/osrm`) |
| `--image IMAGE` | OSRM image. Must be the tag compose serves |
| `--threads N`, `--build-mem SIZE` | Build threads (default: all cores), memory cap per build container (default `6g`; exit 137 inside = out of memory) |
| `--canary-port PORT`, `--smoke "lon,lat;lon,lat[;...]"` | Test server port (default 5002) and smoke-route points. The default points are in central Bucharest; pass your own for other regions |
| `--compose-file FILE`, `--compose-env FILE`, `--service NAME` | After switching, recreate this compose service (default `osrm-foot`) and wait until it is healthy |
| `--keep N` | Older datasets to keep besides `current` (default 2) |
| `--force` | Rebuild even if this dataset exists (e.g. after an image upgrade) |
| `--no-switch` | Build and canary-test only; leave `current` as it is |
| `--rollback` | Point `current` back to `previous` (§5.7) |

**What a run does:**
1. Fetches Geofabrik's `.md5`. Downloads the extract only if it changed (cached in `downloads/`), then verifies the
   md5.
2. Clips with osmium when `--bbox` is given.
3. Runs `osrm-extract -p /opt/foot.lua --data_version <id>`, then `osrm-partition` and `osrm-customize`. These use
   the pinned image, run as your uid, with no network and under `--build-mem`.
4. Makes the dataset world-readable (§5.8).
5. Canary test: starts `osrm-routed` on `127.0.0.1:<canary port>`, hardened exactly like compose. The smoke route
   must return code `Ok`, `data_version = <id>`, one leg per pair of points, and plausible distances (0–20 km) and
   durations (0–4 h). On failure nothing is switched.
6. Switches atomically: `current → datasets/<id>`, `previous →` the old one. Recreates the compose service, which
   re-resolves the symlink. If the service does not become healthy, it switches back and exits 7.
7. Prunes: keeps `current`, `previous` and the `--keep` newest others, plus the 20 newest log directories.

**Layout:**

```
/opt/osrm/
  downloads/   europe_romania-latest.osm.pbf (+ .md5, .date)
  datasets/    <id>/region.osrm.*, BUILD_PARAMS, BUILD_INFO [, ROLLED_BACK]
  logs/        <run>/<step>.log
  current  ->  datasets/<id>        mounted read-only into osrm-foot as /data
  previous ->  datasets/<old id>
```

**Exit codes:**

| Code | Meaning |
| --- | --- |
| 0 | OK, or up to date |
| 2 | Usage error, or a conflicting dataset |
| 3 | Prerequisites missing (tools, docker, base dir, disk, image) |
| 4 | Download or integrity check failed |
| 5 | Clip, extract, partition or customize failed |
| 6 | Canary failed; nothing was switched |
| 7 | Switch or recreate failed; switched back |
| 1 | Unexpected error |

### 5.6 `data_version`

- **What it is.** The dataset id, `<name>-<YYMMDD of the extract>`: for example `romania-260930`,
  `romania-clip-261001` or `bucharest-clip-261002`. It is passed to `osrm-extract --data_version`, and every OSRM
  answer carries it.
- **How the service learns it:**
  - from route answers;
  - from a background `GET /nearest` probe, at most every `WALK_ROUTER_PROBE_S` (60 s) per worker. The probe asks
    about a stop that was routed, never about the user's start.
- **Where it appears:**
  - `versions.routing` in plan and edit responses;
  - `/v1/meta` → `routing.dataset` and `routers[osrm].data_version`;
  - the **leg-cache namespace**: `wr:v1:<data_version>:...`, with `unknown` until the first answer.

  A new dataset therefore starts with an empty cache namespace. Old entries are no longer read; they expire through
  their TTL (Redis) or LRU eviction (L1).

### 5.7 Updates and rollback

- **Routine update.** Re-run the same command monthly, for example from cron. Walking routes change slowly, so
  monthly is enough.
- **During the recreate**, for a few seconds, OSRM is unavailable. Requests then fall back to ORS or the estimate,
  and are flagged. Zero downtime would need two OSRM services (blue/green), which is not set up.
- **Rollback:**

  ```bash
  deploy/osrm/prepare_osrm.sh --rollback --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env
  ```

  This sets `current` to `previous` and recreates the service. The dataset rolled away from gets a `ROLLED_BACK`
  marker, so a later cron run will not switch back to it. To use that dataset anyway, use `--force`, delete the
  marker, or run `--rollback` again.
- **Image upgrade.** Change `osrm-foot.image` in compose and `--image` together, then run with `--force`. A dataset
  built by another OSRM version must not be served.

### 5.8 Permissions and hardening

**How `osrm-foot` runs in compose:**
- as the image's root user, but with `cap_drop: [ALL]`, `no-new-privileges`, a read-only root filesystem, a 16 MB
  tmpfs at `/tmp`, and `init`;
- the dataset is bind-mounted read-only with `create_host_path: false`, so a missing `current` symlink fails loudly
  instead of becoming an empty root-owned directory;
- port 5000 is only exposed on the Docker network, never published. OSRM has no authentication.

**Why the dataset must be world-readable.** Without capabilities, root can read only what the "other" permission
bits allow. `osrm-extract` writes `region.osrm.fileIndex` as 0700, so the script runs `chmod -R a+rX` on every new
dataset, on every switch and on the "up to date" path. That last one repairs datasets built by older versions of the
script. Without it, `osrm-routed` exits with
`File /data/region.osrm.fileIndex mapping failed: ... Permission denied`. Docker Desktop on macOS hides this
problem; Linux hosts show it. A dataset copied from another machine needs the same command:
`chmod -R a+rX datasets/<id>`.

**Other rules:**
- The canary runs with the same hardening, so any dataset it accepts is one `osrm-foot` can serve.
- The build containers run as the invoking uid, with `--network none` and `--memory 6g`.
- `/opt/osrm` and `datasets/` must be traversable by others (755).
- The compose health check sends `GET /nearest/v1/foot/26.1025,44.4355` (central Bucharest) over bash's `/dev/tcp`
  and expects `"Ok"`. Change the point if the dataset does not include Bucharest.
- `walk-planner` deliberately has no `depends_on: osrm-foot`, because the chain falls back.

### 5.9 Several cities

- **One dataset for all cities.** One `osrm-routed` serves one dataset, and the service has a single
  `WALK_ROUTER_URL`.
- **Cities in the same country.** Build the whole country (no `--bbox`), or a box that covers all the cities.
- **Cities in different countries.** Clip each city with a margin and merge the clips into one file, then build that
  file. For example:

  ```bash
  cd /opt/osrm/downloads      # extracts downloaded from download.geofabrik.de (pick the region containing each city)
  OSM="docker run --rm -u $(id -u):$(id -g) -v $PWD:/data -w /data sloco-osmium-tool:trixie"
  $OSM extract --bbox 25.80,44.20,26.40,44.70 --strategy complete_ways -o bucharest.osm.pbf europe_romania-latest.osm.pbf
  $OSM extract --bbox <W,S,E,N of city 2> --strategy complete_ways -o city2.osm.pbf <its extract>.osm.pbf
  $OSM merge bucharest.osm.pbf city2.osm.pbf -o cities.osm.pbf
  cd - && deploy/osrm/prepare_osrm.sh --pbf /opt/osrm/downloads/cities.osm.pbf --name cities \
      --compose-file deploy/docker-compose.yml --compose-env /opt/sloco-data/walk/walk.env
  ```

  - The `sloco-osmium-tool:trixie` image exists once the script has run with `--bbox`.
  - `--smoke` must use points in one city. The default points are in Bucharest.
  - Clip big countries rather than building them whole: a foot extract of all of Germany needs an estimated 35 GiB
    of RAM.
- **Points outside the dataset** get `NoSegment`, so their legs become estimates and are flagged. They never get a
  wrong route.

### 5.10 Smoke test from the planner container

```bash
docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml exec walk-planner \
  python -m walk_planner route --coords "44.4355,26.1025;44.43311,26.098698;44.423987,26.107657"
```

The command uses the container's routing environment. Add `--json` to get the legs, the events and the full status.

| | Expected output |
| --- | --- |
| Healthy (v26.10.0 foot) | `quality streets`; legs of about 667 m / 8.1 min and 1,803 m / 21.6 min; plus the `data_version` |
| OSRM down, no ORS | `quality estimate`; legs of 543 m / 7.2 min and 1,673 m / 22.3 min; the event `osrm connect`; `streets available: True` until the breaker opens |

---

## 6. ORS: the cloud fallback

### 6.1 Host and request

```
POST {ORS_BASE_URL}/v2/directions/foot-walking/geojson
Authorization: <ORS_API_KEY>          body: {"coordinates": [[lon, lat], ...]}
```

- **Host.** The default is `https://api.heigit.org/openrouteservice`.
  - The old host `api.openrouteservice.org` has been deprecated since 2026-04-28, has had only 10% of the plan quota
    since 2026-08-27 (about 200 Directions requests a day), and is due to shut down.
  - The old host was the likely cause of the quota running out on 2026-09-26.
  - Keys work on both hosts.
- **Requests.**
  - At most 50 points per request. Longer routes are split into chunks that share their boundary point (120 points
    → 50 / 50 / 22).
  - Legs are split out of one answer using `way_points` and `segments`.
- **Timeouts and retries.** Timeouts are 2 s to connect and 8 s to read, both capped by the request budget. There
  are **no retries**: every retry would cost quota.
- **The foot-walking profile** also walks at 5 km/h, so ORS times match OSRM's.

### 6.2 Key handling

- **The key is a secret.** Put it only in `/opt/sloco-data/walk/walk.env` (`chmod 600`). Never put it under compose
  `environment:`, on a command line, or in the app.
- **The service never exposes it.**
  - It is not a service setting, so it never appears in `/v1/meta`; `/v1/meta` shows only `key_configured: true`.
  - `RoutingConfig.__repr__` prints `<set>`.
  - Error details never contain it.
  - URLs in `/v1/meta` and in logs are stripped of `user:password@`, query string and fragment.
- **Use a production key of its own**, not the research dashboard's key, so testers cannot use up the production
  fallback.
- **Do not paste `docker compose config` output.** It prints the merged environment, including the key.

### 6.3 Quotas and the local limiter

- **The plan.** ORS Standard (free): 2,000 Directions requests a day and 40 a minute (sliding window), per key.
- **The local limiter, per worker process.** The defaults are 35 a minute and 1,800 a day.
  - With `WEB_CONCURRENCY` workers, divide the limits by the number of workers. [deploy/env.example](../deploy/env.example)
    sets 17 and 900 for 2 workers.
  - A request the limiter blocks is not sent. It appears as the event `ors rate_limited`, detail `local limiter`, and
    does not open the breaker.
  - Every attempt uses a slot, including attempts that then fail to connect.
  - The windows restart with the process.
- **Expected use while OSRM is down.** About one request per variant (3 per plan by default; more for routes over 50
  points) and one per edit. 2,000 a day is roughly 500–600 plan builds.
- **Quota headers.** The answers' `x-ratelimit-remaining`, `x-ratelimit-limit` and `x-ratelimit-reset` are recorded
  and shown in `/v1/meta` under `routers[ors].quota`.

### 6.4 Error behaviour

| ORS answer | Error kind | Circuit breaker | Effect |
| --- | --- | --- | --- |
| 403 (daily quota used up) | `quota` | Opens until `x-ratelimit-reset` (clamped to 60 s–24 h), or 1 h without the header | ORS skipped; legs estimated |
| 429 (per-minute limit) | `rate_limited` | Opens for `Retry-After` (1–300 s), or 60 s | same |
| 401 (key rejected) | `auth` | Opens for 1 h | same; check the key |
| A non-200 answer below 500 that is not an ORS error (e.g. an HTML 404 from a wrong `ORS_BASE_URL`) | `config` | Opens for 1 h | same; check `ORS_BASE_URL` |
| 5xx, timeout, connection error | `http_5xx` / `timeout` / `connect` | Opens for 5 min after 2 in a row | same |
| ORS error 2010 (point not found) / 2009 (route not found) | `no_segment` / `no_route` | none (the server is healthy) | Chain stops; estimate |
| Other ORS error codes | `bad_request` | none | Estimate |
| No key | `disabled` | none | ORS is not in the chain at all |

### 6.5 Attribution, terms, privacy

- **Attribution.** Wherever ORS routes are displayed, show exactly **"© openrouteservice.org by HeiGIT | Map data ©
  OpenStreetMap contributors"**. The client knows from `segments[].provider == "ors"` (§4.2). The research took this
  text from summaries of HeiGIT's terms, so confirm it on the terms page together with the commercial-use question.
- **Commercial use.** It is unclear whether the free Standard plan allows commercial use. The research could not
  open HeiGIT's terms page. Check the terms, or budget for a paid plan, before launch.
- **Privacy.** ORS is a third party, HeiGIT in Heidelberg.
  - While OSRM is down, the coordinates of the route are sent to ORS. That includes the user's start as the app sent
    it.
  - The service does not round the start, because rounding would change plans compared with the golden outputs.
  - If that is not acceptable, leave `ORS_API_KEY` empty, so the chain is OSRM then estimate. Or have the gateway
    round the start before calling the service: 4 decimals is about 11 m.

---

## 7. Leg cache

**What is cached.** One **street** leg per directed pair of points: duration, distance, geometry and provider.

| | L1 (always) | L2 (optional) |
| --- | --- | --- |
| Where | In-process LRU, per worker | Redis at `WALK_ROUTE_CACHE_REDIS_URL`, shared by every worker and replica |
| Size | 20,000 legs | `maxmemory 256mb`, `allkeys-lru`, no persistence. Use the compose `cache` profile (`redis:7.4.11-alpine`) or a database of the backend's Redis, e.g. `redis://<redis>:6379/5` |
| Access | — | One `MGET` per route; writes pipelined with `SET ... EX <ttl>` |
| Failure | — | 0.25 s connect and read timeouts, no retries, no health checks. Behind its own breaker (3 failures → skip Redis for 30 s). Errors are logged and treated as misses; they never fail a plan |

- **Key:** `wr:v1:{dataset}:{sha1("lat1,lon1>lat2,lon2")}`.
  - Coordinates are rounded to 5 decimals, about 1 m. Keys are directional.
  - `dataset` is the OSRM `data_version`. It is `unknown` until OSRM's first answer, and `ors` when ORS is the only
    router.
- **Value:** `{"d": seconds, "m": metres, "g": polyline6, "p": "osrm"|"ors", "x": expires_at}`, about 0.3–1 KB.
- **TTL:**

  | Leg | TTL | Why |
  | --- | --- | --- |
  | From OSRM | 30 days | A new map version starts a new namespace anyway |
  | From ORS | 24 hours | Replaced by OSRM legs soon after OSRM recovers |
  | Touching the user's start (the first leg, and the last one of a loop) | 1 hour | Privacy |
  | Estimates | never cached | Street routes should appear as soon as a router is back |

- **Effect.**
  - Duplicate variants and repeated plans cost no HTTP.
  - An edit that changes a few legs still costs one request (for the whole route), never one per leg.
  - With 2 or more workers, only Redis shares legs between workers.
- **Privacy.**
  - Legs that touch the start live at most 1 hour, in L1 and in Redis.
  - Keys are SHA-1 hashes of rounded coordinates. That is pseudonymous, not anonymous: someone can hash a grid over
    the city and match. The cached geometry also begins at the start.
  - Treat the Redis database as personal data for that hour: private network only, no persistence, no replicas
    outside the host.
  - Logs never contain precise coordinates:
    - the start is rounded to 2 decimals in access lines;
    - numbers in router error details are masked;
    - transport errors show only the exception class.

---

## 8. Timeouts, retries, circuit breakers, deadline

| | OSRM | ORS | Redis (leg cache) |
| --- | --- | --- | --- |
| Timeouts (connect, read) | 0.5 s, 2.0 s | 2 s, 8 s | 0.25 s, 0.25 s |
| Retries | 1, after a 100–200 ms jittered pause, on connection error, timeout, 502, 503 or 504. OSRM drops idle keep-alive connections after 5 s, so a reused connection can be stale. Never on 400 | none | none |
| Breaker opens | after 3 consecutive failures | after 2 consecutive failures (5xx, timeouts, connection errors); at once on 403, 429, 401 or `config` | after 3 failures |
| Breaker open for | 30 s, then one probe call | 5 min, or as in §6.4 | 30 s |
| Counts as a failure | `timeout`, `connect`, `http_5xx`, `parse` (an answer that is not OSRM's) | the same, plus `quota`, `auth`, `rate_limited`, `config` | any error |
| Counts as healthy | `no_segment`, `no_route`, `bad_request`, `mismatch` (the input was the problem) | ORS error codes | — |

**Other `RoutingError` kinds** that appear in events: `breaker_open` (skipped, no call made), `deadline` (the budget
is used up) and `disabled` (no key).

**Per request.** The routing budget is `WALK_ROUTING_DEADLINE_S` = 6 s, shared by all variants.
- Every attempt's timeouts are capped by what is left.
- Below 50 ms left, no new attempt is started.
- `requests` applies read timeouts per socket read, so an answer that trickles in can overshoot slightly.

**Worst case for one request, when both routers hang:**
- OSRM costs about 1.2 s with connect timeouts (2 × 0.5 s plus the pause), or about 4.2 s with read timeouts
  (2 × 2 s plus the pause).
- ORS gets whatever is left.
- In total, about 6 s; then every remaining leg is estimated.

**Measured with connections refused.** OSRM fails in about 0.14–0.21 s, including the retry pause, and ORS in under
1 ms. Once the OSRM breaker is open (after 3 failing requests on a worker), OSRM costs nothing for 30 s.

**Gateway timeouts:**

| Endpoint | Timeout | Why |
| --- | --- | --- |
| `POST /v1/walks/plan` | at least 15 s | Up to 6 s of routing plus up to about 5 s of planning for 24-hour windows |
| `/schedule`, `/insert` | at least 10 s | Planning takes milliseconds, so this is mostly the routing budget. A shorter timeout, such as 5 s, can cut off an edit while a router hangs |

**Breakers, the ORS limiter, the L1 cache and the counters are per worker process.** With 2 workers, each one learns
on its own, and a restart resets them.

---

## 9. Configuration

The routing settings are environment variables, read by `RoutingConfig.from_env` for each request through
`make_provider()`. They are deliberately not service settings, so `ORS_API_KEY` never reaches `/v1/meta`. Containers
get their environment at start, so a change needs `up -d walk-planner`.

| Variable | Code default | Compose / env.example | Meaning |
| --- | --- | --- | --- |
| `WALK_ROUTER_URL` | unset: no OSRM | The standalone compose file: `http://osrm-foot:5000` when `walk.env` leaves it unset; set it **empty** in `walk.env` to disable OSRM. Merged into the backend's compose without the `environment:` block ([DEPLOY.md §4.2](DEPLOY.md#42-on-the-backends-network)), the code default applies: write the URL out | OSRM base URL. A value without `http(s)://` logs a warning, and every OSRM call then fails and falls back |
| `WALK_ROUTER_PROFILE` | `foot` | — | Profile segment in the OSRM URL |
| `WALK_ROUTER_PROBE_S` | `60` | — | Seconds between background `/nearest` probes per worker (data_version, health); `0` turns them off |
| `WALK_ROUTING_DEADLINE_S` | `6` | — | Routing budget of one request, shared by all its variants. `0` means routers are never called |
| `ORS_API_KEY` | empty: no ORS | **secret**, only in `walk.env` | ORS fallback key |
| `ORS_BASE_URL` | `https://api.heigit.org/openrouteservice` | — | ORS host |
| `ORS_MAX_PER_MIN` | `35` | `17` (2 workers) | Local limiter **per worker**. `0` blocks ORS locally |
| `ORS_MAX_PER_DAY` | `1800` | `900` (2 workers) | Local limiter **per worker** |
| `WALK_ROUTE_CACHE_REDIS_URL` | unset: L1 only | `redis://redis:6379/5` with `--profile cache` | L2 leg cache. Needs the `redis` package, which the image has; without it there is one warning and L1 only. May contain a password: keep it in `walk.env` |
| `WEB_CONCURRENCY` | 2 (image) | `2` | Worker processes. Divide the ORS limits by it |

- **Bad values.** A non-numeric, negative or non-finite number falls back to the default, with one warning per value
  (`routing: ignoring X=...`).
- **Nothing configured** means estimate only, with `chain: ["estimate"]` in `/v1/meta`.

**Typical setups:**

| Setup | `walk.env` |
| --- | --- |
| Production | `WALK_ROUTER_URL` unset (the standalone compose file's default `osrm-foot`; merged: `WALK_ROUTER_URL=http://osrm-foot:5000`), `ORS_API_KEY=<production key>`, `ORS_MAX_PER_MIN=17`, `ORS_MAX_PER_DAY=900`, optionally `WALK_ROUTE_CACHE_REDIS_URL=redis://<redis>:6379/5` |
| OSRM only, no third party | `WALK_ROUTER_URL` unset (merged: written out, as above), `ORS_API_KEY=` empty |
| Estimate only, for `golden run --url` acceptance | `WALK_ROUTER_URL=` empty and `ORS_API_KEY=` empty, or a plain `docker run` without the env file |

---

## 10. Observability

### 10.1 `GET /v1/meta` → `routing`

`routing_status()` returns this block without any network call and without secrets. The values belong to **the
worker that answered**: with 2 workers, two calls can show different breakers and counters. A real example, from a
worker whose OSRM refused connections during one plan (on a test host):

```json
{
  "chain": ["osrm", "estimate"],
  "configured": true,
  "streets_available": true,
  "dataset": null,
  "deadline_s": 6.0,
  "routers": [
    {"name": "osrm", "url": "http://127.0.0.1:9", "profile": "foot", "mode": "by_legs", "data_version": null,
     "breaker": {"state": "closed", "open_for_s": 0.0, "consecutive_failures": 1},
     "last_probe": null}
  ],
  "cache": {"l1_entries": 0, "l1_max": 20000, "redis": "off",
            "hits_l1": 0, "hits_l2": 0, "misses": 15, "writes": 0, "errors": 0},
  "counters": {"cache.miss": 15, "haversine.estimate": 3, "legs.estimate": 15, "osrm.connect": 1,
               "osrm.skipped": 2, "route_legs.estimate": 3}
}
```

| Field | Meaning |
| --- | --- |
| `chain` | The configured order. `["estimate"]` means nothing is configured |
| `streets_available` | At least one router's breaker is not open. It does not mean the router is up |
| `dataset` | The OSRM `data_version` this worker knows |
| `routers[osrm]` | `mode` is `by_legs` or `steps` (an older server); `breaker.state` is `closed`, `open` or `half_open`; `open_for_s`; `last_probe` is `{ok, at, ms, error}` |
| `routers[ors]` | `key_configured`, `breaker`, `limiter.remaining_minute` / `remaining_day` (this worker's local windows), `quota.remaining` / `limit` / `reset_at` (from ORS's headers) |
| `cache` | `redis` is `off`, `on` or `unavailable` (its breaker is open). The hit and miss counts are **per leg** |
| `counters` | Since the worker started (see the table below) |

**Counters:**

| Counter | Counts |
| --- | --- |
| `<provider>.<outcome>` | Chain steps, e.g. `osrm.ok`, `osrm.connect`, `osrm.timeout`, `osrm.no_segment`, `osrm.breaker_open`, `osrm.skipped`, `ors.quota`, `ors.rate_limited`, `haversine.estimate`, `cache.partial` |
| `legs.streets`, `legs.estimate` | Legs |
| `route_legs.streets`, `route_legs.mixed`, `route_legs.estimate` | Assembled routes |
| `cache.hit`, `cache.miss` | Cache lookups. **`cache.hit` also counts one extra per fully cached route** (an event of the same name), so for hit ratios use `cache.hits_l1 + cache.hits_l2` against `cache.misses` |

### 10.2 Access log

Every request writes one JSON line on stdout (logger `walk_planner.service.access`). The routing fields of a plan
request whose OSRM was down:

```json
{"endpoint": "plan", "status": 200, "duration_ms": 212.2, "start": [44.43, 26.1],
 "routing": {"provider": "ChainProvider", "chain": ["osrm"], "spent_ms": 136.1,
             "legs": {"streets": 0, "estimate": 15}, "failed": ["osrm"], "events": 6,
             "event_log": [{"provider": "osrm", "outcome": "connect", "ms": 136.1, "detail": "ConnectionError"},
                           {"provider": "haversine", "outcome": "estimate", "legs": 5},
                           {"provider": "osrm", "outcome": "skipped", "detail": "failed earlier in this request"},
                           {"provider": "haversine", "outcome": "estimate", "legs": 5},
                           {"provider": "osrm", "outcome": "skipped", "detail": "failed earlier in this request"},
                           {"provider": "haversine", "outcome": "estimate", "legs": 5}]},
 "routing_quality": ["estimate"],
 "timings": {"candidates_ms": 31.5, "solver_ms": 31.0, "router_ms": 136.1, "render_ms": 1.1, "plan_ms": 198.9}}
```

| Field | Meaning |
| --- | --- |
| `routing.provider` | `ChainProvider`, or `RoutingProvider` (estimate only, which shows `chain: ["estimate"]`) |
| `routing.chain` | The routers of this request (the estimate is implicit) |
| `routing.spent_ms` | Time spent waiting on routers |
| `routing.legs` | Legs by quality, over all variants |
| `routing.failed` | Routers that failed in this request |
| `routing.data_version` | Present when known |
| `routing.event_log` | **Only when a step failed or legs were estimated**: at most 10 steps, details cut to 200 characters. Routine requests (`ok`, cache `hit` or `partial`, `skipped`) omit it |
| `routing_quality` | One value per variant |
| `timings.router_ms` | Router time; it is subtracted from `solver_ms` |

A healthy line looks like
`"routing": {"provider": "ChainProvider", "chain": ["osrm"], "spent_ms": 14.1, "legs": {"streets": 23, "estimate": 0}, "failed": [], "events": 3, "data_version": "bucharest-clip-261002"}`.

**Other lines from the logger `walk_planner.routing`:**

| Level | Message | When |
| --- | --- | --- |
| WARNING | `router <name> failed: <kind> <detail>` | Every failure except `breaker_open`, `skipped` and `deadline` |
| INFO | `routing fell back to the straight-line estimate for N of M legs` | Every route that used the estimate |
| WARNING | `OSRM probe failed (<url>): <error>` | A failed `/nearest` probe |
| INFO | `OSRM <url> rejects overview=by_legs (older than v26.4): using steps=true` | An older server was detected |
| WARNING | `leg cache: Redis read failed (<exception>); in-process cache only` | Redis read error |
| WARNING | `leg cache: Redis write failed (<exception>)` | Redis write error |
| WARNING, once each | Messages about a `WALK_ROUTER_URL` without `http(s)://`, an ignored numeric value, a missing `redis` package, or an unusable Redis URL | Configuration problems |

### 10.3 Suggested alerts

| Alert | Source | Threshold (start here, tune later) |
| --- | --- | --- |
| Street routing degraded | Access log: share of `plan` lines whose `routing_quality` contains `estimate` or `mixed` | above 5% over 15 min |
| OSRM down | Access log `routing.failed` contains `osrm`, or WARNING `router osrm failed`; `docker ps` health of `osrm-foot` | any for 5 min / unhealthy |
| ORS quota used up or key problem | WARNING `router ors failed: quota` / `auth` / `config` | any |
| ORS fallback in use | `ors.ok` counters, or `segments[].provider == "ors"` in samples | sustained for more than 1 h (OSRM should be fixed) |
| Slow routing | `timings.router_ms` p95 | above 1,000 ms (normal: OSRM about 10 ms per plan, 0 on a cache hit) |
| Redis cache unavailable | `/v1/meta` `cache.redis == "unavailable"`, or WARNING `leg cache: Redis` | any for 5 min (only when Redis is configured) |
| OSRM map stale | `versions.routing` (`<name>-YYMMDD`) older than 45 days | the monthly refresh did not run |

Because `/v1/meta` is per worker, alert on the logs rather than on a single `/v1/meta` sample.

---

## 11. Troubleshooting

**Every plan is `estimate`.**
1. Check `/v1/meta` → `routing.chain`.
   - `["estimate"]` means nothing is configured. Outside compose `WALK_ROUTER_URL` is unset by default; in compose it
     is empty only if `walk.env` sets it empty.
2. Check `routers[osrm].breaker.state` and the access lines' `routing.event_log`. The outcome names the problem:

   | Outcome | Problem |
   | --- | --- |
   | `connect` | Wrong host or port, `osrm-foot` not running, or a different Docker network |
   | `timeout` | Overloaded or hanging server |
   | `http_5xx` | Server error |
   | `parse` | The URL answers but it is not OSRM, e.g. a proxy page |
   | `no_segment` | See the next item |
   | `breaker_open` | Recent failures |
   | `deadline` | The budget was used up |

3. Check the router itself:

   ```bash
   docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml ps osrm-foot
   docker compose ... logs --tail 50 osrm-foot
   docker compose ... exec walk-planner python -m walk_planner route --coords "44.4355,26.1025;44.423987,26.107657" --json
   ```

**OSRM returns `NoSegment`** (event `osrm no_segment`, then the estimate, and ORS is not tried). A point is more than
1 km from any walkable way in the dataset. Usually this is a `--bbox` clip that does not contain the place or the
user's start, for example a start outside Bucharest, or a city that is not in the dataset. Fix: build with a wider
box or Romania-wide (§5.4), or merge the city in (§5.9). A single odd point, such as a start in the middle of a lake,
is expected behaviour: that leg is an estimate and is flagged.

**A breaker is open** (`/v1/meta` `breaker.state: "open"`, events `breaker_open`). This is the normal reaction to
failures; the router is retried automatically after the cooldown:

| Router | Cooldown |
| --- | --- |
| OSRM | 30 s, then one probe call |
| ORS | 5 min, the quota reset, `Retry-After`, or 1 h |

Fix the cause. Restarting the planner (`up -d --force-recreate walk-planner`) resets all breakers at once.

**`osrm-foot` does not start.**
- `... region.osrm.fileIndex mapping failed: ... Permission denied`: run `chmod -R a+rX /opt/osrm/datasets/<id>`, or
  re-run `prepare_osrm.sh`, which repairs it.
- `/opt/osrm/current` missing or dangling: the compose bind mount fails by design. Run `prepare_osrm.sh`. If
  `current` is a real directory, the script stops with exit 3 and tells you to remove it. Docker creates such an
  empty directory when a short-syntax bind mount's source is missing.
- A version mismatch error after an image change: rebuild with `--force`.

**ORS quota used up** (events `ors quota`, `/v1/meta` `routers[ors].breaker.state: "open"`, `quota.remaining: 0`):
- ORS stays off until `quota.reset_at`, and plans are estimates meanwhile.
- The real fix is to bring OSRM back.
- If the local limiter blocks first (`ors rate_limited`, detail `local limiter`), the per-worker limits are
  exhausted. Check that `ORS_MAX_PER_*` × workers stays under the plan.
- A key shared with other users (e.g. the research dashboard) drains the quota: use a production key of its own.

**`ors config` or `ors auth`.** `ORS_BASE_URL` points at something that is not the ORS API (`config`), or the key is
wrong (`auth`). The default host needs no path change: `https://api.heigit.org/openrouteservice`. Both block ORS for
1 h per worker.

**`versions.routing` is `null` although OSRM works.** That worker has not had an OSRM answer yet, for example it
served only cached legs. It fills in after the first OSRM call or probe (`WALK_ROUTER_PROBE_S`). `plan_id` includes
this value, so identical requests can get different `plan_id`s right after a worker starts.

**Redis problems** (`cache.redis: "unavailable"`, `cache.errors` growing). Plans are unaffected; only the shared
cache is lost. Check the URL and network, and that the `redis` service is up (`--profile cache`).

**Slow plans** (`timings.router_ms` high). Look at `event_log` for timeouts. If OSRM is slow, check `docker stats`
(CPU limit 1, memory 2 GB) and whether the dataset is Romania-wide on a small host.

---

## 12. Calibration: estimate versus streets

The v1 routing research measured this on 2026-10-02. The sample was 40 open places within 4 km of Piața
Universității, across themes, and one 40×40 `/table` call to the public FOSSGIS OSRM foot server. That server uses its
own foot profile, which implies about 4.5 km/h. This gives 1,560 ordered pairs, all routable.

"Street / straight" is the street distance divided by the straight-line distance. "Estimate / street time" is our
estimated time divided by the router's time.

| Straight-line distance | Pairs | Street / straight: median (p10–p90) | Estimate / street time: median (p10–p90) | Estimate too short |
| --- | ---: | --- | --- | ---: |
| < 0.5 km | 36 | 1.35 (1.04–1.69) | 0.99 (0.80–1.30) | 50% |
| 0.5–1 km | 118 | 1.27 (1.12–1.51) | 1.05 (0.89–1.20) | 32% |
| 1–2 km | 358 | 1.27 (1.16–1.48) | 1.06 (0.91–1.17) | 28% |
| 2–4 km | 694 | 1.25 (1.18–1.34) | 1.08 (1.00–1.15) | 10% |
| ≥ 4 km | 354 | 1.21 (1.15–1.30) | 1.11 (1.04–1.17) | 1% |
| **All** | 1,560 | **1.244** (1.16–1.38; maximum 2.15) | **1.081** (0.98–1.16) | — |

What follows:

- **The 1.35 detour factor is slightly conservative.** The median street/straight ratio is 1.24.
- **Legs the planner actually uses are safe.** For the 710 pairs whose estimate is 45 min or less (the planner's
  longest single walk), street minus estimate is −1.6 min at the median, −4.5 at p10, +1.9 at p90 and +8.0 at worst.
  No leg the planner thinks fits under 45 minutes actually exceeds it.
- **Barriers raise the factor.** A route across the Dâmbovița (Universitate → Villacrosse → Radu Vodă → Poenaru
  Bordea → back) had street/straight factors of 1.41, 1.46, 1.51 and 1.30: 67.3 min by street at about 4.5 km/h,
  against 63.7 min estimated.
- **Our OSRM walks faster than the estimate.** Our OSRM and ORS both walk at **5 km/h**, while the estimate uses
  4.5 km/h × 1.35. Street times therefore come out about **17% shorter** than the estimate at the median
  (1.24 / 5 against 1.35 / 4.5).
  - With routing on, plans usually finish earlier than the planner intended, and part of the "fill the window" time
    goes unused.
  - This is today's behaviour too (the dashboard had the same with ORS); it is not a regression.
  - Single legs vary around that median. In the smoke route of §5.10, the first leg takes longer by street than
    estimated (8.1 against 7.2 min) and the second takes less (21.6 against 22.3 min).
- **What v1 logs for later decisions.** Access lines carry `routing.legs` and the per-variant `routing_quality`.
  Per-leg logging of the estimate against the street minutes is not implemented; §13 lists it as a v1.1 item.

---

## 13. Known limitations and options

| Item | v1 state | Options |
| --- | --- | --- |
| Speed mismatch | Estimate 4.5 km/h × 1.35; routers 5 km/h | v1.1, a product decision because plans change: calibrate the factor per city from an OSRM `/table` sample (Bucharest 1.24), and/or align the speeds (edit `foot.lua` and rebuild) |
| Router distances in the optimizer | Not used | v2: a matrix provider built per request from OSRM `/table` (CH pipeline, a larger `--max-table-size`), falling back to the estimate. Plans would then depend on the router and on map versions |
| Per-leg calibration logging | Not implemented | Log `estimate_min` against `streets_min` per leg (sampled), to decide the two items above |
| User start sent to ORS unrounded | As sent by the app | The gateway rounds it, or no ORS key (§6.5) |
| Per-worker breakers, limiter, L1, counters | Each worker learns on its own; `/v1/meta` differs between workers | Shared state in Redis; scrape logs instead of `/v1/meta` |
| OSRM switch downtime | A few seconds of flagged fallback | Blue/green services, or `osrm-datastore` with `--shared-memory` |
| Pedestrian squares walked around | Stock `foot.lua` | `foot_area.lua` (experimental) |
| `counters["cache.hit"]` | Over-counts by one per fully cached route | Use the `cache` block (§10.1); fix the counter in `routing.py` |
| Attribution | Not in the API | The client derives it from `segments[].provider` (§4.2) |
