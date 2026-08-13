# TASKS 50: Catalog cities list + hard `city=` cut on the feed

**Status: DONE in code** — awaiting migration `021` on production and a live
re-check of the iOS ask `frontend_new/messages-to-backend-dev/not-done/CITIES_LIST.md`.

iOS Profile needs a city picker whose values are the catalog's own `places.city`
spellings, and Decide must show only that city. `city=` was a +20 fallback boost,
so Tbilisi leaked Bucharest from offset 100. There was no cities endpoint.

## Decisions

- **`GET /v1/cities` is a new `cities` module**, layered like the rest
  (`controller → service → store`). Public. No migration: the store runs
  `GROUP BY city, country` on the direct pg pool (PostgREST would cap at 1000
  rows and miss Tbilisi). Sort: `placeCount` desc, then `name`.
- **`city=` is a hard cut, not a boost, and not a cluster input.** Likes from
  other cities still teach taste. The recommender is not passed a city (its
  request is still place-id lists). Personalized path filters hydrated rows in
  the gateway (same shape as `category`, `TASKS_46`). Fallback path filters
  inside `feed_fallback_places` before scoring (migration `021`, same signature
  as `020` — `CREATE OR REPLACE`, no `DROP`).
- **Unknown names return an empty page**, not 400. `Bucuresti` does not match
  `Bucharest`; 400 would need a live enum that goes stale. Empty is honest.
- **Search `city=` is out of scope.** Decide was the blocked screen.

## Changes

1. `src/modules/cities/` — list endpoint.
2. `feed.service.ts` — `applyFeedCuts` adds the city match after category.
3. Migration `021_feed_fallback_city_cut.sql` — WHERE on `user_city`; drop the
   redundant +20. Rollback: `supabase/rollback/2026-08-13_021_feed_city_cut_rollback.sql`.
4. Docs: `FRONTEND_CITIES_API.md`, `FRONTEND_FEED_API.md`, `DECISIONS.md`,
   `CURRENT_STATE.md`.

## Test Plan

- Cities service maps rows; empty catalog → `{ cities: [] }`.
- `GET /v1/cities` 200 without auth; unversioned path 404.
- OpenAPI contains `/v1/cities`, `CitiesResponse`, `CatalogCity`.
- Personalized `city=Tbilisi` keeps only Tbilisi cards, positional rank 1.
- `city=tbilisí` still matches `Tbilisi`.
- `city=Bucuresti` over a Bucharest-only snapshot does not return those cards
  (empty, via the existing empty-personalized → fallback path).
