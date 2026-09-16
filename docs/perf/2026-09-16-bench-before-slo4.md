## Perf bench — 2026-09-16 17:24 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| search_places cafe @Bucharest | 10.04 s | 9,086 / 73,891 | 484 / 2,075 | 7.63 s | 12.09 s | 12.09 s |
| search_places pizza @Bucharest | 2.67 s | 374 / 12,469 | 0 / 0 | 205 ms | 314 ms | 314 ms |
| search_places cafe @Berlin | 10.28 s | 0 / 82,804 | 484 / 2,077 | 8.40 s | 11.44 s | 11.44 s |
| search_places browse cafe-bucket @Bucharest | 175 ms | 38 / 169 | 0 / 0 | 2 ms | 9 ms | 9 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/search/places?q=cafe @Bucharest | 7.26 s | 9.47 s | 12.39 s | 12.39 s |
| GET /v1/search/places?q=cafe @Berlin | 8.42 s | 7.10 s | 10.95 s | 10.95 s |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
