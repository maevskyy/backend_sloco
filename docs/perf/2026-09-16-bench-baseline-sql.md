## Perf bench — 2026-09-16 15:46 UTC

PostgreSQL 17.6, 5 runs/case.

### SQL (RPC bodies as the gateway calls them)

| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |
|---|---|---|---|---|---|---|
| search_places cafe @Bucharest | 17.49 s | 13,941 / 69,970 | 484 / 2,077 | 8.69 s | 11.51 s | 11.51 s |
| search_places pizza @Bucharest | 2.25 s | 430 / 12,416 | 0 / 0 | 227 ms | 237 ms | 237 ms |
| search_places browse cafe-bucket @Bucharest | 149 ms | 53 / 208 | 0 / 0 | 2 ms | 2 ms | 2 ms |
| feed_fallback_places Bucharest limit 200 | 28.12 s | 15,597 / 53,504 | 0 / 0 | 3.30 s | 14.25 s | 14.25 s |
| feed_fallback_places Berlin cafe-bucket limit 200 | 8.01 s | 10,801 / 36,073 | 0 / 0 | 5.58 s | 10.05 s | 10.05 s |
| feed_places_by_source_ids 200 ids @Bucharest | 3.32 s | 7,601 / 15,345 | 0 / 0 | 1.43 s | 1.88 s | 1.88 s |
| map_tile z13 Bucharest 4689/2965 | 286 ms | 176 / 1,151 | 0 / 0 | 4 ms | 4 ms | 4 ms |
| map_tile z13 Tbilisi 5116/3049 | 38 ms | 22 / 84 | 0 / 0 | 1 ms | 1 ms | 1 ms |
| cities aggregate (GET /v1/cities) | 709 ms | 546 / 1,864 | 0 / 0 | 15 ms | 16 ms | 16 ms |

_SQL: server-side execution time via EXPLAIN ANALYZE; 5 runs per case, first = cold, rest = warm. Blocks are 8 KB._
