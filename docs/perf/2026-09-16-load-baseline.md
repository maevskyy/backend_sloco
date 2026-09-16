# Load baseline — 2026-09-16, before horizontal scaling

Artillery `load/hot-paths.yml` against `https://sloco.pp.ua` from a laptop: one gateway
instance, one recommender instance, Supabase small compute. Prod already had the SQL
fixes 024–027 (SLO-7/5/4) applied — this is the "before" for the scaling work, not for
those fixes (their single-request before/after lives in `2026-09-16-bench-*.md`).

Anonymous traffic only: app open (cities + feed page 1 + page 2), a category pill,
search as typed, map tiles, place details. The signed-in personalized feed (recommender
in the loop, ~1.2 s per serving) is not in the mix — it needs a bearer token.

## Reading

- **The ceiling is ~5 arrivals/s ≈ 6–7 req/s.** At 2/s everything is fine (p95 ≈ 1 s).
  At 5/s p95 jumps to 5–6 s within 20 s and the first requests stop answering within
  15 s. At 20/s half the requests are HTTP 500; at 30/s p50 is pinned at 5.07 s.
- **The pinned 5.07 s is the gateway's own timeout, not the database.** `lib/pg.ts`
  opens `new Pool({ max: 5, connectionTimeoutMillis: 5_000 })`, and cities, tiles,
  search, events and rec-served all share it. Once five queries are in flight every
  further request waits exactly 5 s for a connection and gets a 500. That is why
  `/v1/cities`, `/v1/map/tiles` and `/v1/search/places` — the pg-pool routes — show the
  same p50 and the same 5xx share, while the feed routes (PostgREST, separate pool)
  show none.
- **`GET /v1/cities` is the first thing to fix.** Every app open runs
  `select city, country, count(*) from places group by …` over 58k rows (≈ 200–400 ms)
  and holds one of the five connections for it. Three cities, changes on import only —
  it should be a cached constant (SLO-8).
- **~40 % of requests got no answer in 15 s** (`ERR_SOCKET_TIMEOUT` × 1979). The feed
  routes go through PostgREST, whose own pool queues; their max was 14.9 s — just under
  the client timeout. Beyond the pg pool there is a second queue on the Supabase side.
- **What horizontal scaling changes here:** a second gateway instance brings five more
  pool connections and doubles the ceiling by accident. Raising `max` on one instance
  does the same for free — the honest limit is the Supabase pooler (`max_connections`
  60, Supavisor pool per role), so pool sizes across instances must add up below it.
  The recommender is not in this picture at all; it caps the *personalized* feed at
  ~1 serving/s per instance and is the one place where more instances buy throughput.

## Report

## hot-paths.yml · staircase 2 → 5 → 10 → 20 → 30 arrivals/s · 60 s per step

2026-09-16 18:11 UTC · 289 s · 3960 virtual users · 4868 requests (21.0 req/s mean)

### Overall

| Requests | 2xx | 4xx | 5xx | Errors (no response) | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| 4868 | 1669 | 0 | 1220 | 1979 | 5.07 s | 5.27 s | 7.41 s | 14.88 s |

`ERR_SOCKET_TIMEOUT` × 1979

### Per request

| Request | n | 2xx | 4xx | 5xx | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|
| `GET /v1/cities` | 877 | 495 | 0 | 382 | 5.07 s | 5.17 s | 6.06 s | 8.19 s |
| `GET /v1/feed/places city` | 31 | 31 | 0 | 0 | 1.11 s | 5.07 s | 5.60 s | 14.88 s |
| `GET /v1/feed/places city offset=20` | 29 | 29 | 0 | 0 | 821 ms | 2.28 s | 3.98 s | 10.42 s |
| `GET /v1/feed/places city+category` | 29 | 29 | 0 | 0 | 1.47 s | 5.83 s | 6.31 s | 12.80 s |
| `GET /v1/map/tiles` | 926 | 506 | 0 | 420 | 5.07 s | 5.17 s | 6.19 s | 13.69 s |
| `GET /v1/places/:id` | 86 | 86 | 0 | 0 | 103 ms | 907 ms | 2.10 s | 2.31 s |
| `GET /v1/search/places` | 911 | 493 | 0 | 418 | 5.07 s | 5.94 s | 11.05 s | 14.84 s |

### Timeline (10 s windows)

| t | arrivals/s | req/s | p50 | p95 | p99 | 5xx | no response |
|---|---|---|---|---|---|---|---|
| 00:00 | 1.0 | 1.0 | — | — | — | 0 | 0 |
| 00:00 | 2.0 | 2.4 | 334 ms | 1.30 s | 1.69 s | 0 | 0 |
| 00:10 | 2.0 | 3.0 | 247 ms | 1.02 s | 1.15 s | 0 | 0 |
| 00:20 | 2.0 | 2.8 | 268 ms | 1.02 s | 1.13 s | 0 | 0 |
| 00:30 | 5.0 | 6.4 | 400 ms | 1.25 s | 1.50 s | 0 | 0 |
| 00:40 | 5.0 | 7.1 | 450 ms | 1.72 s | 2.28 s | 0 | 0 |
| 00:50 | 5.0 | 6.2 | 1.20 s | 5.60 s | 6.31 s | 0 | 0 |
| 01:00 | 5.0 | 6.5 | 758 ms | 4.87 s | 6.31 s | 0 | 12 |
| 01:10 | 5.0 | 6.6 | 183 ms | 1.98 s | 2.28 s | 0 | 32 |
| 01:20 | 5.3 | 6.4 | 1.00 s | 4.15 s | 4.87 s | 0 | 29 |
| 01:30 | 10.0 | 11.4 | 561 ms | 1.56 s | 8.69 s | 0 | 29 |
| 01:40 | 10.0 | 12.4 | 789 ms | 2.89 s | 3.07 s | 0 | 35 |
| 01:50 | 10.0 | 11.6 | 290 ms | 1.38 s | 1.79 s | 0 | 39 |
| 02:00 | 10.0 | 13.1 | 187 ms | 1.83 s | 2.28 s | 0 | 49 |
| 02:10 | 10.0 | 12.1 | 215 ms | 1.33 s | 1.94 s | 0 | 50 |
| 02:20 | 11.0 | 13.0 | 1.41 s | 3.26 s | 3.91 s | 0 | 57 |
| 02:30 | 20.0 | 24.3 | 207 ms | 1.72 s | 1.90 s | 0 | 50 |
| 02:40 | 20.0 | 24.1 | 3.91 s | 4.58 s | 4.97 s | 0 | 92 |
| 02:50 | 20.0 | 23.9 | 3.98 s | 5.27 s | 6.31 s | 6 | 89 |
| 03:00 | 20.0 | 25.4 | 4.58 s | 5.60 s | 6.19 s | 3 | 103 |
| 03:10 | 20.0 | 24.6 | 5.07 s | 6.06 s | 6.70 s | 78 | 95 |
| 03:20 | 20.9 | 25.9 | 5.07 s | 5.83 s | 9.05 s | 84 | 113 |
| 03:30 | 30.0 | 35.1 | 5.07 s | 5.17 s | 8.35 s | 159 | 107 |
| 03:40 | 30.0 | 35.7 | 5.07 s | 5.07 s | 8.87 s | 190 | 124 |
| 03:50 | 30.0 | 36.1 | 5.07 s | 5.71 s | 6.19 s | 53 | 153 |
| 04:00 | 30.0 | 37.0 | 5.07 s | 5.17 s | 6.98 s | 179 | 143 |
| 04:10 | 30.0 | 36.6 | 5.07 s | 5.07 s | 5.27 s | 203 | 158 |
| 04:20 | 27.7 | 33.1 | 5.07 s | 5.49 s | 9.42 s | 175 | 149 |
| 04:30 | 0.0 | 3.9 | 5.07 s | 5.38 s | 11.73 s | 90 | 162 |
| 04:40 | 0.0 | 0.0 | — | — | — | 0 | 109 |

