## Perf bench — 2026-09-16 16:16 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| feed_places_by_source_ids 200 ids @Bucharest | 34 ms | 0 / 1,524 | 0 / 0 | 4 ms | 4 ms | 4 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 | 9.26 s | 8.52 s | 8.65 s | 8.65 s |
| GET /v1/feed/places?limit=20&city=Bucharest | 8.81 s | 3.99 s | 9.36 s | 9.36 s |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 5.40 s | 2.57 s | 3.38 s | 3.38 s |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
