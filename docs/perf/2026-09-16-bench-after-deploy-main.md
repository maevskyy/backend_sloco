## Perf bench — 2026-09-16 19:12 UTC

PostgreSQL 17.6, 5 runs/case, HTTP against https://sloco.pp.ua.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| search_places cafe @Bucharest | 438 ms | 0 / 1,554 | 0 / 0 | 87 ms | 87 ms | 87 ms |
| search_places pizza @Bucharest | 137 ms | 0 / 396 | 0 / 0 | 67 ms | 70 ms | 70 ms |
| search_places cafe @Berlin | 368 ms | 0 / 895 | 0 / 0 | 258 ms | 258 ms | 258 ms |
| search_places browse cafe-bucket @Bucharest | 87 ms | 0 / 417 | 0 / 0 | 30 ms | 33 ms | 33 ms |
| feed_fallback_places Bucharest limit 200 | 474 ms | 0 / 1,590 | 0 / 0 | 128 ms | 130 ms | 130 ms |
| feed_fallback_places Berlin cafe-bucket limit 200 | 646 ms | 0 / 2,256 | 0 / 0 | 86 ms | 86 ms | 86 ms |
| feed_places_by_source_ids 200 ids @Bucharest | 355 ms | 114 / 1,410 | 0 / 0 | 4 ms | 4 ms | 4 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._

### HTTP (gateway)

| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|
| GET /v1/feed/places?limit=20 | 1.51 s | 485 ms | 885 ms | 885 ms |
| GET /v1/feed/places?limit=20&city=Bucharest | 674 ms | 423 ms | 430 ms | 430 ms |
| GET /v1/feed/places?limit=20&category=cafe&city=Berlin | 662 ms | 502 ms | 724 ms | 724 ms |
| GET /v1/search/places?q=cafe @Bucharest | 248 ms | 244 ms | 280 ms | 280 ms |
| GET /v1/search/places?q=cafe @Berlin | 440 ms | 438 ms | 968 ms | 968 ms |

_HTTP: wall-clock from this machine against https://sloco.pp.ua; 5 runs per endpoint._
