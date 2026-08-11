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

## Acceptance

After migration `018` + deploy, warm `?q=coffee` from the same vantage point lands around
**0.3–0.45 s** (floor ~0.15 + pool hop + a двузначный-ms query), and `?q=co` stops being an
outlier. Record the before/after table here.

## Follow-ups (not in this task)

- **Feed fallback (2.3 s)** — the anonymous feed scores the whole table per request
  through PostgREST, uncached. Candidates: same pg-pool switch for `feed.store.ts`, and/or
  a short-TTL gateway cache for the anonymous snapshot. Do after measuring post-018
  numbers (the norm columns don't touch this path).
- If search is still query-bound after `018`: `EXPLAIN (ANALYZE, BUFFERS)` the RPC body in
  the SQL editor; the next lever is a KNN-style top-N via a GiST trigram index instead of
  scoring every candidate.
