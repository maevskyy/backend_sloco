# TASKS 45: Search — `category` + `radiusMeters` on `GET /v1/search/places`

**Status: Planned.**

iOS ask `frontend_new/messages-to-backend-dev/not-done/SEARCH_CATEGORY_FILTER.md` — the search
page's seven category chips are blocked on this and deliberately render inert until it ships.
Umbrella plan: `../../../../ios-asks-implementation-plan.md` §6.

## Context (verified in code)

- `search_places` (migration `011`) matches by trigram `word_similarity` over the name and
  `search_keywords` (name+category+primary_type+types+ai_tags), ranked
  `100·text + 30·exact + 15·prefix + 12·city + 6·country + 10·nearby + 8·quality + 5·pop`.
  The name term is an order of magnitude above the proximity boost (`10/(1+km)`), which is
  exactly the iOS probe's finding: a Tbilisi "…Cafe…" **name** match outranks a Bucharest café
  1 545 km closer, `matchReason: "name"` on nearly every hit. There is no radius constraint
  and no category parameter.
- The gateway schema (`search.schemas.ts`) takes `q, lat, lng, city, country, limit` with `q`
  required.

## Decisions

- **Bucket vocabulary lives in the `places` module** (`src/modules/places/common/
  place-buckets.ts`, exported via the module's `index.ts`): seven coarse buckets —
  `cafe, food, bar, culture, nature, shopping, leisure` — each a keyword list matched against
  `category`/`primary_type`/`types`. Search (this task) and feed (`TASKS_46`) both import it;
  no `shared/` bucket (repo rule), and place taxonomy is the places module's domain. Exact
  keyword lists are an implementation detail; **the final vocabulary is sent to iOS** (their
  spec explicitly offers to map the chips onto whatever we pick).
- **Two modes in one RPC**, dispatched on the params:
  - *text mode* (`q` present): today's ranking, byte-identical when the new params are
    absent; `radius_meters`, when given, becomes a hard `st_dwithin` **filter** (not a
    boost — the spec's point) applied alongside;
  - *browse mode* (`category` present, `q` absent): WHERE bucket-match
    (+ `st_dwithin` when `radius_meters` given), ORDER BY `distance_m asc` when an origin
    exists, else `map_visibility_score desc`. A chip is a browse, not a query.
- `q` becomes optional, but **at least one of `q`/`category` is required** (400 otherwise);
  unknown `category` value → 400. Repeatable/CSV `category` accepted (a chip may need
  several buckets).
- Changed RPC body blocked by a feature → legitimate migration.

## Changes

1. **Migration `0NN_search_category_radius.sql`** — `CREATE OR REPLACE
   public.search_places(q, user_lat, user_lng, user_city, user_country, result_limit,
   category_keywords text[] default null, radius_meters integer default null)` with the two
   modes above (drop/recreate with the new signature, grants re-applied; non-destructive).
   Browse-mode bucket match: `primary_type ilike any(...) or category ilike any(...) or
   exists (select 1 from unnest(types) t where t ilike any(...))` — same normalization
   (`f_unaccent`, lower) as the existing keywords.
2. **`src/modules/places/common/place-buckets.ts`** — the bucket → keyword table + a
   `bucketsToKeywords()` helper; export via `places/index.ts`.
3. **Search module** — schema: `q` optional, `category` (array via CSV/repeat, zod-enum of
   the seven buckets), `radiusMeters` (positive int, sane max e.g. 50 000), refine "q or
   category required"; store passes the two new RPC params; `matchReason` for browse-mode
   rows: the RPC returns `'category'`.
4. Docs: `docs/FRONTEND_SEARCH_API.md` (both modes + the vocabulary), `DECISIONS.md` row,
   vocabulary reply into the iOS spec file.

## Test Plan

- `pnpm build && pnpm test && pnpm lint`; search service/routes tests: mode dispatch, 400s
  (neither param; unknown category), pass-through of both new params.
- Live acceptance (the spec's own probe, from Bucharest centre):
  `?category=cafe&lat=44.4325&lng=26.1039&radiusMeters=1500&limit=20` → only cafés within
  1.5 km, nearest-first, zero Tbilisi rows;
  `?q=cafes` (no category) → byte-identical to today.
- iOS after ship: wires the seven chips (tracked on their side as "do NOT fake via `q`").

## Dependencies

- None upstream. `TASKS_46` reuses `place-buckets.ts` from here — do this one first.

## Out Of Scope

- Feed category filtering (`TASKS_46`).
- Ranking changes in text mode; pagination; new response fields.
