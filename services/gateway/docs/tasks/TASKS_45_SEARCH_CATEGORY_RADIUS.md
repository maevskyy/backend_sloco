# TASKS 45: Search — `category` + `radiusMeters` on `GET /v1/search/places`

**Status: DONE** — shipped and verified in production 2026-08-12.

Live acceptance (Bucharest centre): `?category=cafe&radiusMeters=1500` returns 20 cafés,
**all in Bucharest** (the ask's core complaint — zero Tbilisi), all inside the radius,
nearest-first, `matchReason: category`, `query: ""`. Per-bucket spot checks are clean:
`bar` → Lounge/Wine bar/Pub/Karaoke bar, `culture` → theatres and concert halls,
`leisure` → night clubs/comedy/escape rooms, `cafe` → Cafe/Coffee shop/Tea house/Bakery.
Word-boundary matching holds (no "barbecue" in `bar`, no "coffee shop" in `shopping`).
Validation: neither `q` nor `category` → 400, unknown bucket → 400, `q`+`category` → 200,
plain `q` unchanged. Browse latency **0.18–0.22 s** (see `TASKS_48`).

Two rounds of live fixes were needed after the first deploy — both in migration `020` and
the bucket table: matching the `types` attribute bag put restaurants in `nature`, and the
bare `garden` keyword then pulled in beer gardens. Both closed; beer gardens now resolve
to `bar` (verified on "London Garden", id 10957).

**Catalog reality to pass to the client:** within 5 km of Bucharest centre the buckets
hold 50+/50+/50+/50+/50+ for cafe/food/bar/culture/leisure but **1 for `shopping` and 0
for `nature`** — that data simply is not in the two-city catalog. The buckets stay in the
contract; hiding those two chips is a client-side product call.

Implementation notes vs the plan below:

- Migration `018_search_category_radius_norms.sql` also carries the `TASKS_48` performance
  half (stored `*_norm` columns + trigger + backfill + column index) — one rewrite of
  `search_places` instead of two back-to-back.
- Bucket matching is **word-boundary**, not substring (`' '||col||' ' like '% kw %'`) —
  substring matching put "barbecue restaurant" into `bar` and "coffee shop" into
  `shopping`. Covered by unit tests (`places/tests/place-buckets.test.ts`).
- The vocabulary lives in `places/common/place-buckets.ts` (seven buckets), exported via
  the places `index.ts`; keyword lists are data-driven from the live catalog. **Honesty
  note for iOS:** `nature` and `shopping` are near-empty in the current two-city catalog
  (no parks/malls were ingested) — the buckets exist for contract stability, but their
  chips will return few or no results until the catalog grows. Casinos/gambling/adult
  venues are deliberately in no bucket.
- The search store also moved off PostgREST onto the direct pg pool (`TASKS_48`).
- Response `query` echoes `""` in browse mode (the field stays a non-null string for the
  existing client decoder).
- **Backward compatible with the currently deployed gateway.** The new params carry
  `DEFAULT NULL`, so the old 6-named-argument PostgREST call still resolves, the defaults
  reproduce the old behaviour, and the returned columns are unchanged — running `018`
  before deploying does not break production search (it only makes it faster).
- **Rollback:** `supabase/rollback/2026-08-11_018_019_rollback.sql` restores the `011`/`016`
  function bodies verbatim. No data is at risk: `018` writes only the columns it adds.

iOS ask `frontend_new/messages-to-backend-dev/done/SEARCH_CATEGORY_FILTER.md` — the search
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
   modes above (drop/recreate with the new signature, grants re-applied — the drop is why
   the migration carries a CAPS warning in its header).
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
