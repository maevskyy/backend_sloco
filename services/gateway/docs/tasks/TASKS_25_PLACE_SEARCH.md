# TASKS 25: Place Search

## Summary

Add global fuzzy search by places. Search must query all `public.places`, not
only the current map bbox/cluster. It should be tolerant to typos, partial input,
and accents, and it should rank results with location context: places in the
current city / nearby area first, then other relevant places.

The MVP engine is PostgreSQL `pg_trgm` through Supabase RPC. No separate
Elasticsearch, Meilisearch, Typesense, vector search, or autocomplete service in
this task.

## Context

Current map APIs are optimized for rendering visible pins. They intentionally
return a limited set for a bbox and should not become the search engine.

Search has a different job:

- search across all `places`;
- support typo/fuzzy matching;
- support accent-insensitive matching;
- rank locally relevant results higher;
- return a slim result list;
- let the frontend open full details through `GET /v1/places/:placeId`.

Confirmed product/technical decisions:

- search surface: `name`, `category`, `primary_type`, `types`, `ai_tags`;
- location context: optional `lat`/`lng` from the client;
- city/country context: optional soft boost, not a hard filter;
- database engine: Postgres `pg_trgm`;
- implementation style: thin MVP module similar to `map`.

## Database Migration

Create:

```text
supabase/migrations/011_search_places_rpc.sql
```

### Extensions

Enable:

```sql
create extension if not exists pg_trgm;
create extension if not exists unaccent;
```

Important Supabase note: verify the schema where `unaccent` is installed before
using it in the function body. If the extension lives outside `public`, adjust
the function accordingly.

### Immutable Unaccent Wrapper

Add an immutable wrapper so normalized expressions can be indexed:

```sql
create or replace function public.f_unaccent(text)
returns text
language sql
immutable
parallel safe
strict
return public.unaccent('public.unaccent', $1);
```

### Keyword Column And Trigram Indexes

Keep `name` separate from the keyword blob so name similarity is not diluted by
long text.

Primary name index:

```sql
create index if not exists places_name_trgm
  on public.places
  using gin (lower(public.f_unaccent(name)) gin_trgm_ops);
```

Secondary keyword search column and index:

```sql
alter table public.places
  add column if not exists search_keywords text;

create or replace function public.build_place_search_keywords(...)
returns text;

create or replace function public.set_place_search_keywords()
returns trigger;

update public.places
set search_keywords = public.build_place_search_keywords(...);

create trigger places_set_search_keywords
before insert or update of name, category, primary_type, types, ai_tags
on public.places
for each row
execute function public.set_place_search_keywords();

create index if not exists places_search_keywords_trgm
  on public.places
  using gin (search_keywords gin_trgm_ops);
```

Important implementation note: do not use `generated always as (...) stored` for
`search_keywords`. PostgreSQL rejects generated expressions that are not strictly
immutable, and the normalized keyword expression with `unaccent` / array fields
can fail with `generation expression is not immutable`. A normal column with
backfill + trigger is more boring and more reliable for Supabase.

## RPC

Create:

```sql
public.search_places(
  q text,
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
  result_limit integer default 20
)
```

Function type: `plpgsql`, `stable`.

### Query Normalization

Normalize `q`:

```sql
q_norm = lower(public.f_unaccent(q))
```

The backend validates `q` as 2..100 chars before calling the RPC.

### Candidate Selection

Use index-assisted fuzzy candidates:

```sql
lower(public.f_unaccent(name)) %> q_norm
or search_keywords %> q_norm
```

Add an `ILIKE` / prefix fallback for shorter queries if trigram candidate recall
is too low.

Set a local trigram threshold inside the function:

```sql
perform set_config('pg_trgm.word_similarity_threshold', '0.3', true);
```

Threshold is tunable after real data checks.

### Scoring

Use an explicit tunable score formula.

Inputs:

- `text_match`:
  ```sql
  greatest(
    word_similarity(q_norm, name_norm),
    0.6 * word_similarity(q_norm, search_keywords)
  )
  ```
- `exact_name_boost`: `name_norm = q_norm`;
- `prefix_name_boost`: `name_norm like q_norm || '%'`;
- `same_city_boost`: `user_city` is provided and normalized `city` equals
  normalized `user_city`;
- `same_country_boost`: `user_country` is provided and normalized `country`
  equals normalized `user_country`;
- `nearby_boost`: if `user_lat`/`user_lng` are provided:
  ```sql
  1 / (1 + st_distance(p.geom::geography, origin) / 1000)
  ```
- `quality`: `map_visibility_score / 100`;
- `popularity`: `popularity_score_0_100 / 100`.

Final formula:

```text
score =
  w1 * text_match
  + w2 * exact_name_boost
  + w3 * prefix_name_boost
  + w4 * same_city_boost
  + w5 * same_country_boost
  + w6 * nearby_boost
  + w7 * quality
  + w8 * popularity
```

Initial weights should be documented inside the SQL comment and treated as
tunable. City/country/coordinates are boosts only, not filters. The city is a
priority, not a prison.

### Result Ordering

Clamp limit:

```sql
least(greatest(coalesce(result_limit, 20), 1), 50)
```

Order:

```sql
order by score desc, id asc
```

### Returned Shape

Return a lightweight set, similar to `map_places_in_bbox`, plus search-specific
fields:

- `id`;
- `name`;
- `category`;
- `primary_type`;
- `city`;
- `country`;
- `formatted_address`;
- `latitude`;
- `longitude`;
- `rating`;
- `price_level`;
- `primary_photo_*` fields from `place_photos`;
- `distance_m`;
- `match_reason` = `name` | `category` | `type` | `tag`;
- internal `score` / `relevance` only if useful for debugging.

Grant execute consistently with existing RPCs.

## Backend API

Add:

```http
GET /v1/search/places
```

Query params:

```text
q        required, string 2..100
lat      optional, finite number
lng      optional, finite number
city     optional, string
country  optional, string
limit    optional, int 1..50, default 20
```

This endpoint is public, but it supports optional auth so saved state can be
enriched for logged-in users. Invalid auth should return `401`, matching `map`.

Example:

```http
GET /v1/search/places?q=coffee&lat=44.43&lng=26.10&city=Bucharest&country=RO&limit=20
```

Response:

```json
{
  "query": "coffee",
  "places": [
    {
      "id": 123,
      "name": "Origo Coffee",
      "category": "cafe",
      "primaryType": "cafe",
      "city": "Bucharest",
      "country": "RO",
      "latitude": 44.437,
      "longitude": 26.101,
      "rating": 4.7,
      "priceLevel": 2,
      "primaryPhoto": null,
      "distanceMeters": 830,
      "matchReason": "name",
      "isSaved": false
    }
  ]
}
```

When the user selects a result, the frontend should call:

```http
GET /v1/places/:placeId
```

## Backend Module

Create:

```text
src/modules/search/
```

Mirror the current `map` module style.

### Files

```text
src/modules/search/common/search.schemas.ts
src/modules/search/common/search.openapi.ts
src/modules/search/common/search.types.ts
src/modules/search/common/search.mappers.ts
src/modules/search/stores/search.store.ts
src/modules/search/services/search.service.ts
src/modules/search/controllers/search.controller.ts
src/modules/search/search.module.ts
src/modules/search/index.ts
```

### Schemas

`searchPlacesQuerySchema`:

- `q`: string, 2..100;
- `lat?`: coerced finite number;
- `lng?`: coerced finite number;
- `city?`: string;
- `country?`: string;
- `limit?`: int 1..50, default 20.

`searchPrimaryPhotoSchema`:

- same shape as map primary photo, but own `$id` so search does not depend on
  map schemas.

`searchPlaceResultSchema`:

- `id`;
- `name`;
- `category`;
- `primaryType`;
- `city`;
- `country`;
- `latitude`;
- `longitude`;
- `rating`;
- `priceLevel`;
- `primaryPhoto` nullable;
- `distanceMeters` nullable;
- `matchReason`;
- `isSaved`.

`searchPlacesResponseSchema`:

```json
{
  "query": "coffee",
  "places": []
}
```

Registry ids:

```text
SearchPlacesQuery
SearchPrimaryPhoto
SearchPlaceResult
SearchPlacesResponse
```

### OpenAPI

`common/search.openapi.ts`:

- use `buildComponentSchemas(registry)`;
- use `makeDefineRoute({ tag: "Search", security: false, errorResponses: { 400, 401, 500 } })`;
- expose `searchPlacesRouteSchema`;
- querystring references `SearchPlacesQuery`;
- response references `SearchPlacesResponse`.

### Types

`common/search.types.ts`:

- `SearchPlaceRow` for RPC rows;
- `SearchPlaceResult`;
- `SearchPlacesResult`;
- `SearchPlacesQuery`;
- `SearchStoreContract`;
- `SearchPlacesService`.

### Mapper

`mapSearchRowToResult`:

- maps snake_case RPC row to API camelCase;
- converts `distance_m` to `distanceMeters`;
- maps photo fields to `primaryPhoto`;
- preserves nullable fields;
- defaults `isSaved` to `false`.

### Store

`SearchStore.searchPlaces({ q, lat, lng, city, country, limit })`:

- calls `getSupabaseClient().rpc("search_places", ...)`;
- wraps call in:
  ```ts
  measureDependencyMetric({
    dependency: "supabase",
    operation: "rpc",
    name: "search_places",
  })
  ```

### Service

`createSearchPlacesService(store)`:

- calls store;
- maps rows;
- enriches saved state through `savedPlacesService.getSavedPlaceIds`, same as map.

### Controller

Flow:

1. parse query with zod;
2. call `authGuard.optionalUser`;
3. invalid auth => `401`;
4. call service;
5. enrich saved state when user exists;
6. `logResponseSummary`;
7. `handleCommonError(..., "Invalid search query")`.

### Module

`registerSearchModule` with DI:

- `searchPlacesService?`;
- `authService?`;
- `savedPlacesService?`.

## Wiring

Update:

- `src/config/routes.ts`:
  - `AppRoute.SearchPlaces = "/search/places"`;
  - `VersionedAppRoute.searchPlaces`;
- `src/app.ts`:
  - register `registerSearchModule` with `prefix: API_PREFIX`;
  - pass `authService`;
  - pass `savedPlacesService`;
  - support DI override for tests;
- `src/config/swagger.ts`:
  - include `searchComponentSchemas`;
  - add tag `Search`;
- `docs/tasks/README.md`:
  - add `TASKS_25_PLACE_SEARCH.md`;
- frontend docs:
  - create `docs/FRONTEND_SEARCH_API.md` or update
    `docs/FRONTEND_MAP_API.md`.

## Optimizations

Do in v1:

- location as soft boost, not hard filter;
- small limit: default 20, max 50;
- index-assisted candidates;
- slim response only;
- full data through `GET /v1/places/:placeId`.

Defer:

- hard radius prefilter with global fallback;
- separate autocomplete endpoint;
- full-text `tsvector`;
- aliases / synonyms such as `coffee -> cafe`;
- semantic/vector search;
- personalized taste ranking;
- search history / analytics.

## Verification

### Database

Apply migration in Supabase and run:

```sql
select *
from public.search_places('kafe', 44.43, 26.10, 'Bucharest', 'RO', 20);
```

Expected:

- typo-ish queries find relevant places;
- accent-insensitive search works;
- nearby / same-city results rank higher when location context is provided;
- search still returns global results when local results are weak.

Use `explain analyze` on representative queries to verify trigram indexes are
used.

### Backend

Run:

```bash
pnpm typecheck
pnpm test
pnpm lint
pnpm build
```

### Tests

Controller tests similar to `map.routes.test.ts`:

- `200` with results;
- `isSaved` is enriched for authorized user;
- invalid token returns `401`;
- empty / too short `q` returns `400`;
- public request without token works.

Mapper tests:

- maps snake_case row to camelCase result;
- maps nullable photo to `primaryPhoto: null`;
- maps photo fields to `primaryPhoto`;
- maps `distance_m` to `distanceMeters`;
- keeps `isSaved: false` by default.

OpenAPI tests:

- `/v1/swagger/openapi.json` includes `SearchPlaces*` components;
- existing component ids do not change unexpectedly;
- route is documented as `GET /v1/search/places`.

### Manual API Check

```bash
curl "https://sloco.pp.ua/v1/search/places?q=coffee&lat=44.43&lng=26.10&city=Bucharest&country=RO&limit=20"
```

## Assumptions

- `places` is the canonical serving table.
- The endpoint does not depend on current map bbox.
- Current dataset size is still reasonable for Postgres trigram search.
- Search results are intentionally slim.
- Full place details remain served by `GET /v1/places/:placeId`.
- This is fuzzy lexical MVP search, not final Google-grade semantic search.
