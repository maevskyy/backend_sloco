## Perf bench — 2026-09-16 16:52 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| feed_fallback_places Bucharest limit 200 | 566 ms | 0 / 1,656 | 0 / 0 | 122 ms | 126 ms | 126 ms |
| feed_fallback_places Berlin cafe-bucket limit 200 | 446 ms | 0 / 2,139 | 0 / 0 | 86 ms | 86 ms | 86 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 | 1.70 s | 582 ms | 810 ms | 810 ms |
| GET /v1/feed/places?limit=20&city=Bucharest | 616 ms | 409 ms | 421 ms | 421 ms |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 621 ms | 492 ms | 515 ms | 515 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
