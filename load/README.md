# Load Testing

Artillery scenarios that bombard the backend hot paths. The goal is to measure and
defend latency under load, not to discover the app works once.

## Run

Local stack (`make up` first, in another shell):

```bash
make load                          # from backend/, targets http://127.0.0.1:3000
make load BASE_URL=https://sloco.pp.ua   # against production (use sparingly)
```

Or directly:

```bash
cd load
npx artillery@^2 run -t http://127.0.0.1:3000 map-places.yml
```

## What It Hits

- `GET /v1/map/places` — the hot path. Each virtual user draws a different bbox + zoom
  from `viewports.csv`, so we exercise spatial queries across viewports, not one
  cached query. Weight 8.
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
- Do not run the heavy phases against production by default — point at a staging box
  or the local stack.
