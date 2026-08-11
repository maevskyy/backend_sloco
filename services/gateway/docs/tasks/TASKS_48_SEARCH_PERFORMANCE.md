# TASKS 48: Search latency — measured, diagnosed, two fixes

**Status: In progress** — both fixes implemented 2026-08-11 (they ship inside the
`TASKS_45` change set); remaining: run migration `018`, deploy, and re-measure.

Kirill's report: "поиск работает очень медленно". Measured live 2026-08-11 before any
changes.

## Measurements (before)

| leg | endpoint | latency |
|---|---|---|
| client → gateway floor | `GET /v1/health` | ~0.15 s |
| + Supabase round trip (PostgREST) | `GET /v1/health/supabase` | ~0.26 s warm, 1.0 s cold |
| + the search query itself | `GET /v1/search/places?q=coffee…` | **0.6–0.94 s warm, 2.2–4.0 s spikes** |
| short/broad query | `?q=co` | **1.1 s warm** |
| (for later) feed fallback | `GET /v1/feed/places` anonymous | **2.3 s** |

So the search query adds **0.4–0.8 s of real work** on top of the transport floor, and the
transport itself (gateway → Supabase HTTP API) adds ~0.11 s plus cold-connection spikes.

## Diagnosis

1. **Query CPU:** `search_places` (migration `011`) recomputed
   `lower(f_unaccent(...))` over five fields (name/category/primary_type/types/ai_tags)
   for **every candidate row on every query**, then ran 5–6 `word_similarity` calls per
   row. Short queries ("co") match thousands of candidates → thousands of redundant
   normalizations per keystroke.
2. **Transport:** the store called the RPC through the Supabase HTTP API (PostgREST) —
   an extra HTTP hop with its own connection churn — while the gateway already holds a
   direct Postgres pool (`lib/pg.ts`, used by the tile store since `TASKS_32`).

## Fixes (implemented)

1. **Precomputed normalization** (migration `018_search_category_radius_norms.sql`,
   shared with `TASKS_45`): `name_norm / category_norm / primary_type_norm / types_norm /
   ai_tags_norm` become stored columns maintained by the existing search-keywords trigger
   (backfilled for all rows); `search_places` scores against the stored columns; the name
   trigram index moves to the stored column (`places_name_norm_trgm` replaces the
   expression index).
2. **Direct Postgres for search** (`search.store.ts`): the RPC now runs on the shared
   `pg` pool instead of PostgREST — same pattern as `map-tile.store.ts`. Persistent
   connections also remove the cold-spike class.

## Measurements after `018` + deploy (2026-08-11, connection reused)

| query | before | after `018` | verdict |
|---|---|---|---|
| `q=coffee` | 0.6–0.94 s (spikes 2.2–4.0) | **0.36–0.40 s** | fixed |
| `q=bar` | ~0.5 s | **0.30–0.35 s** | fixed |
| `q=pizza` / `q=wine` | 0.34–0.42 s | **0.26–0.34 s** | fixed |
| `q=cafe` | — | 0.63 s | acceptable, watch |
| `q=co` (2 chars) | ~1.1 s | **0.87–1.42 s** | ❌ still slow |
| `category=cafe` (browse, new) | — | **2.9–4.3 s** | ❌ worst endpoint |

TLS handshake to this vantage point is ~0.10 s and the gateway floor ~0.15 s, so the
fixed queries are now essentially floor + a двузначный-ms query. The two failures are
different problems:

**Browse mode (fixed in `020`, pending deploy).** With no text query the function still ran
the whole scoring pipeline (six `word_similarity` calls per row against a NULL query),
computed `st_distance` for every row, and filtered with
`st_dwithin(geom::geography, …)` — a cast no existing index could answer, so every chip tap
scanned the catalog. Migration `020` gives browse mode its own branch: no scoring, KNN
ordering via `geom::geography <-> origin` backed by a new functional GiST index
(`places_geog_gist`), plus a trigram index on `primary_type_norm` for the bucket LIKE.
Expected after deploy: well under 0.3 s.

**Short queries (`q=co`, open).** The trigram path matches thousands of candidates and
scores each one. Not addressed here — options, cheapest first: raise
`pg_trgm.word_similarity_threshold` for very short queries; pre-cut candidates by
`map_visibility_score` before scoring; or a materialized top-N. Needs
`EXPLAIN (ANALYZE, BUFFERS)` from the SQL editor first — do not guess.

## Measurements after `020` (2026-08-12)

Browse mode: **0.19–0.24 s**, and it holds under 8 concurrent requests — down from
2.9–4.3 s. The KNN branch + `places_geog_gist` did it.

Remaining, both about the **text** path:

- `q=co` still ~0.9–1.4 s (unchanged — `020` did not touch text scoring).
- Under 6 concurrent `q=coffee`, latency degrades to 1.0–2.1 s while cached tile requests
  stay at 0.13 s. The gateway's pg pool is `max: 5` (`lib/pg.ts`) and text search now
  holds a connection for the duration of its scoring, so search competes with itself and
  with tiles. Raising the pool is the obvious first lever, but fix the query cost first —
  a bigger pool just buys more parallel slow queries.

## Follow-ups (not in this task)

- **Feed fallback (2.3 s)** — the anonymous feed scores the whole table per request
  through PostgREST, uncached. Candidates: the same pg-pool switch for `feed.store.ts`,
  and/or a short-TTL gateway cache for the anonymous snapshot.
- **Short-query cost** — see above.
