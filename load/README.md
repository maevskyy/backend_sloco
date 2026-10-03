# Load Testing

Artillery scenarios that bombard the backend hot paths. The goal is to measure and
defend latency under load, not to discover the app works once.

## Run

Local stack (`make up` first, in another shell):

```bash
make load                          # from backend/, targets http://127.0.0.1:3000
make load BASE_URL=https://sloco.pp.ua   # against production (use sparingly)
make load-tiles BASE_URL=https://sloco.pp.ua
make load-tiles-record BASE_URL=https://sloco.pp.ua
```

Or directly:

```bash
cd load
npx artillery@^2 run -t http://127.0.0.1:3000 map-places.yml
node gen-tiles.mjs > tiles.csv
npx artillery@^2 run -t https://sloco.pp.ua tiles.yml
```

## API hot paths — `hot-paths.yml`

The throughput baseline for the whole read API as the app uses it, anonymous:
app open (`/v1/cities` + feed page 1 + page 2), a category pill, search as typed
(`queries.csv` mixes 2–3 letter prefixes with words), map tiles for three cities,
place details (`places.csv`). Phases are a staircase — 2, 5, 10, 20, 30 arrivals/s,
60 s each — so the timeline in the report shows the rate at which p95 and 5xx take
off. No `ensure` gate: this file measures, it does not assert.

```bash
make load-hot BASE_URL=https://sloco.pp.ua          # run + print the markdown report
cd load && node report.mjs out/hot-paths.json       # re-render a saved run
```

Results are kept in `docs/perf/` (`2026-09-16-load-baseline.md` is the first one:
ceiling ≈ 5 arrivals/s, pinned by the gateway's `pg` pool of 5). Re-run it after any
change to pool sizes, instance counts or the routes above and add the new file next to
it — same scenario, same steps, comparable numbers.

The signed-in personalized feed is not in the mix: it needs a bearer token. To add it,
put a token into the environment and give the feed requests
`headers: { authorization: "Bearer {{ $env.SLOCO_BEARER }}" }` in a copy of the
`app-open` scenario.

## Real session from the gateway log — `prod-session.mjs`

The opposite of a synthetic run: what the app actually sent while someone used it,
and what each response was made of. The gateway logs every request (`request completed`)
and every dependency call it made for it (`dependency metric`: Supabase select/rpc/auth,
`pg`, recommender) with a `reqId`; the script joins them into a timeline and a per-URL
breakdown — calls per request, sum of call time vs response time (equal means strictly
sequential), token check cost, cache hit/miss for the feed.

```bash
ssh sloco 'cd /opt/backend_sloco && docker compose logs backend --no-log-prefix --since 2026-09-16T19:47:00Z' > session.log
node load/prod-session.mjs session.log             # timeline + breakdown
node load/prod-session.mjs session.log --shapes    # breakdown only
```

`lat`/`lng`/`q` are masked, ids collapsed to `:id`. The log has no client IP or user id,
so pick a quiet window and note who was tapping. First run: 2026-09-16 19:47–19:50 UTC,
64 requests — findings and follow-ups in Linear SLO-37.

## Same API before and after — `golden-diff.mjs`

Not a load test: a contract check for changes that should be invisible to the app
(SLO-49 moved every store from supabase-js/PostgREST to a direct `pg` pool — numbers
that used to come back as JSON numbers can silently turn into strings, timestamps into
another format). The script sends the same GET list to two gateways, one request at a
time, and compares status codes and JSON bodies deeply and type-strictly: `4.5` vs
`"4.5"` is a difference, missing key vs `null` is a difference, array order counts, key
order does not. MVT tiles are compared by length + sha256. Exit code 1 on any difference.

```bash
node load/golden-diff.mjs --a https://sloco.pp.ua --b http://127.0.0.1:3000 --requests load/golden/places.txt
node load/golden-diff.mjs --a https://sloco.pp.ua --b http://127.0.0.1:3000 --requests load/golden/feed-places.txt --self-check
SLOCO_BEARER=… node load/golden-diff.mjs --a https://sloco.pp.ua --b http://127.0.0.1:3000 --requests load/golden/me.txt
```

Request lists live in `load/golden/` (`feed-places.txt`, `places.txt`, `map.txt`,
`me.txt`): one `GET /v1/...` per line, `#` comments, a leading `AUTH` adds
`Authorization: Bearer $SLOCO_BEARER` (`--bearer-env` to use another variable) — use a
test user's token. Only GET/HEAD are accepted, so a list can never write.

Feed meta `generatedAt`, `expiresAt`, `requestId`, `cacheStatus` are ignored by default:
they change per call or depend on each gateway's own cache. `--ignore k1,k2` adds keys
(any depth), `--no-default-ignore` drops the defaults. DB timestamps (`savedAt`,
`createdAt`, …) are compared on purpose. `--self-check` first calls A twice per request
and lists paths that differ between identical calls (ranking ties, clocks); those show
up as `flaky` in the A-vs-B pass and don't fail it. Both gateways must point at the same
database, or every ranking difference will be data, not code.

## What It Hits (`map-places.yml`, `tiles.yml`)

- `GET /v1/map/places` — the hot path. Each virtual user draws a different bbox + zoom
  from `viewports.csv`, so we exercise spatial queries across viewports, not one
  cached query. Weight 8.
- `GET /v1/map/tiles/{z}/{x}/{y}.mvt` — the production map hot path. `gen-tiles.mjs`
  generates Bucharest XYZ tiles into `tiles.csv`; the scenario accepts both 200
  and 204 because empty MVT tiles are valid.
- `GET /v1/feed/places` — recommendation/fallback path. Weight 2.

`viewports.csv` is Bucharest-area today. Add rows for new cities as coverage grows.

Place-details (`GET /v1/places/:id`) is not included yet because it needs real place
IDs; add a CSV of known IDs and a scenario when needed.

## SLOs

The run **fails** if these are breached (see `ensure.thresholds` in `map-places.yml`):

| Metric | Target |
| --- | --- |
| `GET /v1/map/places` p95 | < 150 ms |
| `GET /v1/map/places` p99 | < 400 ms |

These are starting targets. Tune them once we have a real baseline from the first
sustained run, and record the chosen RPS the targets hold at.

## Notes

- The `sustained` phase drives 30 arrivals/sec for 120s. Raise `arrivalRate` to find
  the breaking point; watch the self-hosted Grafana dashboards (TASKS_31) for host
  CPU / container CPU spikes during the run.
- For tile tests, run once after flushing `tile:v*` keys if you want a cold-cache
  DB stress test, then run again without flushing to measure Redis/warm-cache behavior.
  With Artillery Cloud:
  `make load-tiles-record BASE_URL=https://sloco.pp.ua`.
- Do not run the heavy phases against production by default — point at a staging box
  or the local stack.
