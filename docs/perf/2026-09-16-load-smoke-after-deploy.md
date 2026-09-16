# Post-deploy load smokes — 2026-09-16, prod switched to `main` (`aa7b81d`)

Same `load/hot-paths.yml`, phases overridden to 30 s @ 2/s + 30 s @ 5/s. Run #1 started ~3 min after the deploy finished, #2 ~7 min after. Both are far worse than the morning baseline at the same rates (p95 ≈ 1 s there); the HTTP bench 12 minutes later was back to pre-deploy numbers (`2026-09-16-bench-after-deploy-main-http-2.md`). Reading: the first ~10 minutes after a deploy are a degradation window — recommender loading ~350 MB of artifacts on the small host, gateway caches cold — not a code change (runtime code is byte-identical to the previous `dev` deploy).

## Smoke #1 — 3 min after deploy

2026-09-16 19:13 UTC · 75 s · 210 virtual users · 281 requests (5.0 req/s mean)

### Overall

| Requests | 2xx | 4xx | 5xx | Errors (no response) | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| 281 | 194 | 0 | 0 | 87 | 854 ms | 5.71 s | 9.80 s | 12.37 s |

`ERR_SOCKET_TIMEOUT` × 87

### Per request

| Request | n | 2xx | 4xx | 5xx | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| `GET /v1/cities` | 55 | 55 | 0 | 0 | 198 ms | 2.57 s | 3.01 s | 3.05 s |
| `GET /v1/feed/places city` | 16 | 16 | 0 | 0 | 1.79 s | 9.80 s | 9.80 s | 12.37 s |
| `GET /v1/feed/places city offset=20` | 14 | 14 | 0 | 0 | 2.06 s | 6.57 s | 6.57 s | 8.55 s |
| `GET /v1/feed/places city+category` | 6 | 6 | 0 | 0 | 1.62 s | 5.60 s | 5.60 s | 5.73 s |
| `GET /v1/map/tiles` | 31 | 31 | 0 | 0 | 257 ms | 2.62 s | 2.89 s | 3.10 s |
| `GET /v1/places/:id` | 10 | 10 | 0 | 0 | 659 ms | 3.75 s | 3.75 s | 6.68 s |
| `GET /v1/search/places` | 62 | 62 | 0 | 0 | 1.13 s | 4.87 s | 8.19 s | 11.02 s |

### Timeline (10 s windows)

| t | arrivals/s | req/s | p50 | p95 | p99 | 5xx | no response |
|---|---|---|---|---|---|---|---|
| 00:00 | 1.0 | 0.0 | — | — | — | 0 | 0 |
| 00:00 | 2.0 | 2.5 | 321 ms | 854 ms | 1.44 s | 0 | 0 |
| 00:10 | 2.0 | 3.5 | 561 ms | 3.75 s | 3.98 s | 0 | 0 |
| 00:20 | 2.0 | 3.1 | 1.86 s | 6.70 s | 8.52 s | 0 | 0 |
| 00:30 | 5.0 | 6.7 | 805 ms | 6.57 s | 9.80 s | 0 | 1 |
| 00:40 | 5.0 | 5.7 | 539 ms | 2.62 s | 8.19 s | 0 | 14 |
| 00:50 | 4.9 | 6.6 | 1.72 s | 4.58 s | 6.98 s | 0 | 31 |
| 01:00 | 0.0 | 0.0 | — | — | — | 0 | 30 |
| 01:10 | 0.0 | 0.0 | — | — | — | 0 | 11 |


## Smoke #2 — 7 min after deploy

2026-09-16 19:15 UTC · 77 s · 210 virtual users · 285 requests (3.0 req/s mean)

### Overall

| Requests | 2xx | 4xx | 5xx | Errors (no response) | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| 285 | 204 | 0 | 0 | 81 | 686 ms | 10.41 s | 13.50 s | 13.91 s |

`ERR_SOCKET_TIMEOUT` × 81

### Per request

| Request | n | 2xx | 4xx | 5xx | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| `GET /v1/cities` | 52 | 52 | 0 | 0 | 194 ms | 1.33 s | 1.47 s | 1.52 s |
| `GET /v1/feed/places city` | 23 | 23 | 0 | 0 | 5.38 s | 13.23 s | 13.23 s | 13.91 s |
| `GET /v1/feed/places city offset=20` | 14 | 14 | 0 | 0 | 2.19 s | 10.41 s | 10.41 s | 13.38 s |
| `GET /v1/feed/places city+category` | 10 | 10 | 0 | 0 | 7.41 s | 13.50 s | 13.50 s | 13.72 s |
| `GET /v1/map/tiles` | 45 | 45 | 0 | 0 | 279 ms | 1.53 s | 1.69 s | 1.87 s |
| `GET /v1/places/:id` | 14 | 14 | 0 | 0 | 646 ms | 4.87 s | 4.87 s | 6.96 s |
| `GET /v1/search/places` | 46 | 46 | 0 | 0 | 907 ms | 4.40 s | 8.02 s | 8.86 s |

### Timeline (10 s windows)

| t | arrivals/s | req/s | p50 | p95 | p99 | 5xx | no response |
|---|---|---|---|---|---|---|---|
| 00:00 | 3.0 | 5.0 | 743 ms | 1.56 s | 1.56 s | 0 | 0 |
| 00:02 | 2.0 | 2.6 | 758 ms | 7.56 s | 7.56 s | 0 | 0 |
| 00:12 | 2.0 | 3.0 | 743 ms | 7.56 s | 8.02 s | 0 | 0 |
| 00:22 | 2.9 | 3.7 | 488 ms | 2.95 s | 7.41 s | 0 | 0 |
| 00:32 | 5.0 | 7.0 | 354 ms | 5.17 s | 5.38 s | 0 | 0 |
| 00:42 | 5.0 | 6.5 | 1.33 s | 13.50 s | 13.77 s | 0 | 13 |
| 00:52 | 3.5 | 4.7 | 207 ms | 925 ms | 7.41 s | 0 | 30 |
| 01:03 | 0.0 | 0.0 | 11.73 s | 11.73 s | 11.73 s | 0 | 30 |
| 01:12 | 0.0 | 0.0 | — | — | — | 0 | 8 |

