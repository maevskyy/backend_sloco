## Perf bench — 2026-09-16 17:31 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| search_places cafe @Bucharest | 553 ms | 316 / 248 | 0 / 0 | 87 ms | 90 ms | 90 ms |
| search_places pizza @Bucharest | 145 ms | 40 / 356 | 0 / 0 | 66 ms | 66 ms | 66 ms |
| search_places cafe @Berlin | 1.08 s | 818 / 77 | 0 / 0 | 255 ms | 256 ms | 256 ms |
| search_places browse cafe-bucket @Bucharest | 94 ms | 21 / 387 | 0 / 0 | 30 ms | 30 ms | 30 ms |
| feed_fallback_places Bucharest limit 200 | 655 ms | 350 / 1,240 | 0 / 0 | 122 ms | 127 ms | 127 ms |
| feed_fallback_places Berlin cafe-bucket limit 200 | 1.07 s | 617 / 1,639 | 0 / 0 | 87 ms | 97 ms | 97 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/search/places?q=cafe @Bucharest | 660 ms | 237 ms | 453 ms | 453 ms |
| GET /v1/search/places?q=cafe @Berlin | 411 ms | 407 ms | 412 ms | 412 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
