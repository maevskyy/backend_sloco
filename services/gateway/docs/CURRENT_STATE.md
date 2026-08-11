# Current State

This is the first context file to read. It summarizes the current truth of the
backend without the historical task-plan noise.

## Runtime

```text
Production API: https://sloco.pp.ua
Runtime host: Hetzner Ubuntu server
App runtime: Docker Compose + Nginx
Deploy: GitHub Actions -> GHCR -> SSH -> docker compose
Database: Supabase managed Postgres
Cache: Redis (place details, map tiles)
Observability: self-hosted Grafana + Loki + Prometheus (root compose observability profile; TASKS_31)
Recommendation service: private Python service on the Docker network (feed personalization)
Auth direction: iOS Supabase Auth SDK + backend JWT validation
```

The old Lightsail server is deprecated and should only be treated as temporary
rollback context if it still exists.

## Main Commands

```bash
pnpm dev
pnpm build
pnpm test
pnpm lint
pnpm typecheck
```

Data mapper commands (`sloco_ai` is the primary source; see `../scripts/README.md`):

```bash
pnpm map:sloco /path/to/handoff/catalog/locations_combined_food_ttd.csv --out dumps/sloco_places.csv
pnpm map:tripadvisor dumps/raw_tripadvisor_restaurants_import.csv --out dumps/tripadvisor_places.csv
pnpm map:osm dumps/bucharest_cafes.csv --out dumps/osm_bucharest_places.csv
```

Production smoke checks:

```bash
curl https://sloco.pp.ua/v1/health
curl https://sloco.pp.ua/v1/health/supabase
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
curl "https://sloco.pp.ua/v1/search/places?q=coffee&lat=44.43&lng=26.10&city=Bucharest&country=RO"
```

## Active API

```http
GET /v1/health
GET /v1/health/supabase
GET /v1/me
POST /v1/onboarding/complete
GET /v1/me/reactions
GET /v1/me/saved/ids
PUT /v1/me/places/:placeId/reaction
DELETE /v1/me/places/:placeId/reaction
GET /v1/me/saved
GET /v1/me/saved/collections/:collectionId
POST /v1/me/saved/places
DELETE /v1/me/saved/places/:placeId
POST /v1/me/saved/collections
PATCH /v1/me/saved/collections/:collectionId
DELETE /v1/me/saved/collections/:collectionId
POST /v1/me/saved/collections/:collectionId/places
DELETE /v1/me/saved/collections/:collectionId/places/:placeId
PATCH /v1/me/saved/collections/:collectionId/places/order
GET /v1/map/places?swLat=...&swLng=...&neLat=...&neLng=...&zoom=...
GET /v1/map/config
GET /v1/map/tiles/:z/:x/:y.mvt?v=...
GET /v1/places/:placeId
GET /v1/search/places?q=...&category=...&radiusMeters=...&lat=...&lng=...
GET /v1/feed/places?limit=...&offset=...&lat=...&lng=...&sort=...&category=...&debug=...
GET /v1/swagger/docs
GET /v1/swagger/openapi.json
```

OpenAPI is the contract source of truth for frontend agents:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

Human docs for the map endpoint:

```text
docs/FRONTEND_MAP_API.md
```

Human docs for the search endpoint:

```text
docs/FRONTEND_SEARCH_API.md
```

Human docs for the Decide feed endpoint:

```text
docs/FRONTEND_FEED_API.md
```

## Active Database

```text
Serving table: public.places
Auth profile table: public.profiles
Saved places table: public.saved_places
Saved collections table: public.saved_collections
Saved collection membership table: public.saved_collection_places
Place photos table: public.place_photos (R2 public_url; primary + bounded photos[])
Reactions table: public.place_reactions (favorite|dislike|hide, keyed by source_id)
Map pin RPC: public.map_places_in_bbox(sw_lat, sw_lng, ne_lat, ne_lng, result_limit)
Map tile RPC: public.map_tile(z, x, y) (MVT bytea; min-score floor via map_tile_min_score(z))
Place detail RPC: public.place_details_by_id(place_id)
Search RPC: public.search_places(q, user_lat, user_lng, user_city, user_country, result_limit)
Feed hydration RPC: public.feed_places_by_source_ids(source_ids, user_lat, user_lng, result_limit)
Feed fallback RPC: public.feed_fallback_places(user_lat, user_lng, user_city, user_country, result_limit)
Migrations: supabase/migrations/
```

The iOS map renders from the vector tiles (`/v1/map/config` + `/v1/map/tiles`,
Redis-cached, versioned by `MAP_TILE_VERSION`); `/v1/map/places` remains the
bbox JSON endpoint. Rich place data is fetched only after a user selects a
place via `GET /v1/places/:placeId`.
Place search is global, fuzzy, and independent from the current map bbox. It has two
modes: text (`q`) and category browse (`category` without `q`, for the chips), with an
optional hard `radiusMeters` cut. Text matching is trigram — it finds names, not intent
(`TASKS_49`). The seven-bucket vocabulary lives in
`src/modules/places/common/place-buckets.ts` and is shared with the feed's `category`.
Decide feed reads are card-oriented, recommendation-service backed for
authenticated users, and fall back to quality/visibility picks for anonymous or
cold-start users. The ranked snapshot is 200 places deep and can be served in
`relevance` (default) or `distance` order via `sort`. Reactions
(`favorite|dislike|hide`) seed the personalization signals, hard-exclude
disliked/hidden places, and are echoed on feed cards and place details.
Map tiles are capped per tile by `mapVisibilityScore` (6/10/15/25 by zoom band,
uncapped from z18).
Place details carry address, opening hours, phone, website, price level and the
Google Maps URI (imported 2026-08-11 from the raw scrape; `businessStatus` is
still empty — absent at the source).
Place details now expose both `primaryPhoto` and a bounded `photos[]` gallery
list with direct R2 `public_url` values for fullscreen gallery clients.

## Active Data Flow

```text
provider dump CSV
  -> scripts/integrations/<provider>/map.ts
  -> dumps/*_places.csv
  -> manual Supabase import into public.places
  -> GET /v1/map/places
```

Current providers:

```text
Sloco AI catalog (primary) -> scripts/integrations/sloco/map.ts  (source = "sloco_ai", source_id = Google CID)
TripAdvisor -> scripts/integrations/tripadvisor/map.ts
OpenStreetMap -> scripts/integrations/osm/map.ts
```

Place photos are R2 objects indexed into `public.place_photos` by
`pnpm photos:index-sloco` (see `../scripts/README.md`).

## Active Module Architecture

New or rewritten product modules use a lightweight layered OOP shape:

```text
src/modules/<feature>/
  index.ts
  <feature>.module.ts
  controllers/
  services/
  stores/
  common/
  tests/
```

Dependency direction:

```text
controller -> service -> store
```

`src/modules/saved-places/` is the reference implementation. All product modules
(`map`, `me`, `health`, `saved-places`, `places`, `search`, `feed`, `reactions`)
use this shape; `auth` stays a shared service (no HTTP) with its Supabase call
isolated in a store.

Shared code is split by responsibility: `src/lib/` (infrastructure adapters),
`src/config/` (wiring, plus the `openapi.ts` zod→component generator and
`http-schemas.ts` shared error schemas), and `src/http/` (controller glue:
`docsRoute`, `handleCommonError`, `createAuthGuard`, `logResponseSummary`). There
is no `shared/`/`utils/` bucket. OpenAPI components are generated from zod per
module, so request validation and docs cannot drift.

## Current Priorities

- **All nine iOS asks are shipped** (2026-08-12) — `frontend_new/messages-to-backend-dev/
  not-done/` is empty; the cross-service record is `../../../ios-asks-implementation-plan.md`
  (task files `TASKS_38`, `TASKS_41`–`47`, recommendation `TASKS_6`). Personalization is
  live end to end: onboarding picks become favourites and the feed returns `personalized`
  on v4.
- Open next: search quality (`TASKS_49` — text search matches names, not intent; needs a
  product decision) and search latency on the text path (`TASKS_48`).
- Keep backend deploy simple: one Hetzner host, Docker Compose, managed
  Supabase.
- Keep map API lightweight: tiles + bbox pins; rich data stays behind
  `GET /v1/places/:placeId`.
- Keep task docs as history/plans; prefer this file for current context.
- Do not self-host Postgres during MVP unless there is a real business or cost
  reason.
