## Perf bench — 2026-09-16 15:50 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| cities aggregate (GET /v1/cities) | 715 ms | 0 / 2,410 | 0 / 0 | 15 ms | 15 ms | 15 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 ⚠ HTTP 500 | 9.99 s | 8.78 s | 8.93 s | 8.93 s |
| GET /v1/feed/places?limit=20&city=Bucharest | 8.67 s | 7.52 s | 8.98 s | 8.98 s |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 8.68 s | 3.29 s | 6.39 s | 6.39 s |
| GET /v1/search/places?q=cafe @Bucharest | 17.60 s | 8.26 s | 11.29 s | 11.29 s |
| GET /v1/cities | 1.27 s | 167 ms | 172 ms | 172 ms |
| GET /v1/map/tiles/13/4689/2965.mvt | 102 ms | 103 ms | 108 ms | 108 ms |
| GET /v1/places/5021 | 1.71 s | 105 ms | 108 ms | 108 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
