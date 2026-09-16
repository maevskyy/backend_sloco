## Perf bench — 2026-09-16 19:19 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| cities aggregate (GET /v1/cities) | 12 ms | 0 / 143 | 0 / 0 | 12 ms | 12 ms | 12 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 | 3.45 s | 666 ms | 3.78 s | 3.78 s |
| GET /v1/feed/places?limit=20&city=Bucharest | 1.05 s | 543 ms | 656 ms | 656 ms |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 1.54 s | 512 ms | 573 ms | 573 ms |
| GET /v1/search/places?q=cafe @Bucharest | 545 ms | 243 ms | 289 ms | 289 ms |
| GET /v1/search/places?q=cafe @Berlin | 513 ms | 410 ms | 432 ms | 432 ms |
| GET /v1/cities | 272 ms | 163 ms | 166 ms | 166 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
