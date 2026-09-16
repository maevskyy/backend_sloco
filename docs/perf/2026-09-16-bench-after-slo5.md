## Perf bench — 2026-09-16 16:31 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| feed_fallback_places Bucharest limit 200 | 896 ms | 42 / 8,374 | 0 / 0 | 141 ms | 161 ms | 161 ms |
| feed_fallback_places Berlin cafe-bucket limit 200 | 8.44 s | 10,693 / 6,384 | 0 / 0 | 226 ms | 3.53 s | 3.53 s |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 | 3.94 s | 1.30 s | 2.14 s | 2.14 s |
| GET /v1/feed/places?limit=20&city=Bucharest | 888 ms | 438 ms | 662 ms | 662 ms |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 876 ms | 648 ms | 835 ms | 835 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
