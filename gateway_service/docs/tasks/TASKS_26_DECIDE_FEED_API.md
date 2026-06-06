# TASKS 26: Decide Feed API

## Summary

Add a public Gateway API for the iOS `Decide for me` screen:

```http
GET /v1/feed/places
```

This endpoint is the integration boundary between the Swift card pager and the
private `recommendation-service`. The frontend should not call the
recommendation service directly, should not know about embedding artifacts, and
should not stitch recommendation ids to place details itself.

The endpoint returns a pre-ranked batch of place cards. Even though the UI shows
roughly one and a half cards at a time, the backend should return a small batch
so swiping is instant and does not trigger the recommendation algorithm on every
gesture.

## Context

Current frontend state:

- `DecideScreen.swift` uses `Catalog.places`;
- it filters by category and sorts by `Place.match`;
- it renders a vertical card pager with the current card and a peek of the next
  card;
- it expects card-ready fields such as `match`, `why`, `blurb`, `tags`,
  `rating`, `budget`, and `primary/hero` visual data.

Current backend state:

- Gateway exposes map/search/place/saved APIs.
- `recommendation-service` is private on the Docker network and already exposes
  personalized recommendations.
- Gateway currently has only `health()` in `src/lib/recommendation-client.ts`.
- Saved places store internal numeric `places.id`, while the recommendation
  service works with external `place_id` values such as Google `source_id`.

## Product Decision

`Decide for me` is not map rendering.

- `GET /v1/map/places` answers: “what pins should be visible in this bbox?”
- `GET /v1/feed/places` answers: “what should this user pick next?”

Do not overload the map endpoint with recommendation behavior.

## API Contract

Add:

```http
GET /v1/feed/places
Authorization: Bearer <supabase_access_token> optional
```

Query params:

```text
limit    optional int, default 20, min 1, max 50
lat      optional finite number
lng      optional finite number
city     optional string
country  optional string
debug    optional boolean, default false
```

Auth behavior should match map/search:

- no token: return a non-personalized fallback feed;
- valid token: use saved places as recommendation signals;
- invalid token: return `401 Unauthorized`.

The endpoint should be usable before login, but logged-in users should get the
personalized path.

## Response Shape

Return a middle-weight card payload: richer than map pins, lighter than full
place details.

Example:

```json
{
  "feed": {
    "personalizationStatus": "personalized",
    "cacheStatus": "miss",
    "algorithmVersion": "embedding_recommender_v1",
    "embeddingRunId": "20260531T173837Z",
    "generatedAt": "2026-06-01T10:00:00.000Z",
    "expiresAt": "2026-06-01T10:10:00.000Z"
  },
  "inputSummary": {
    "favouritesCount": 8,
    "wantToGoCount": 3,
    "validInputCount": 10,
    "invalidPlaceIds": []
  },
  "places": [
    {
      "id": 123,
      "source": "google",
      "sourceId": "ChIJ...",
      "name": "Origo Coffee",
      "country": "RO",
      "city": "Bucharest",
      "category": "cafe",
      "primaryType": "coffee_shop",
      "latitude": 44.43,
      "longitude": 26.1,
      "rating": 4.7,
      "priceLevel": 2,
      "numberOfReviews": 120,
      "mapVisibilityScore": 91,
      "matchScore": 94,
      "rank": 1,
      "whyRecommended": "Because this matches places you saved.",
      "blurb": "Specialty coffee spot with a calm work-friendly vibe.",
      "tags": ["quiet", "specialty coffee", "work-friendly"],
      "distanceMeters": null,
      "primaryPhoto": {
        "path": "google/...",
        "url": "https://...",
        "width": 1200,
        "height": 900,
        "source": "review"
      },
      "isSaved": false
    }
  ]
}
```

Possible `personalizationStatus` values:

```text
personalized
anonymous_fallback
no_signals_fallback
empty_recommendation_fallback
recommendation_service_fallback
```

Possible `cacheStatus` values:

```text
hit
miss
bypass
not_applicable
```

The exact OpenAPI schema is the frontend source of truth.

## Recommendation Service Contract

Extend `src/lib/recommendation-client.ts` with:

```ts
personalizedPlaces(input: PersonalizedRecommendationRequest)
```

Gateway calls:

```http
POST http://recommendation-service:8000/v1/recommendations/personalized
```

Request:

```json
{
  "user_id": "supabase-user-uuid",
  "favourites_place_ids": ["ChIJ..."],
  "want_to_go_place_ids": ["ChIJ..."],
  "limit": 30,
  "exclude_input_places": true,
  "debug": false
}
```

Response currently contains:

```text
user_id
algorithm_version
embedding_run_id
input_summary
recommendations[] { rank, place_id, score, similarity? }
```

Gateway should request more ids than the public limit when possible, because
some returned `place_id` values may fail hydration if the Gateway `places` table
is missing that source id. For example:

```text
internalRecommendationLimit = min(limit * 2, 50)
```

Then hydrate and cut to the requested public `limit`.

## Saved Signal Strategy

Recommendation service expects two signal lists:

```text
favourites_place_ids
want_to_go_place_ids
```

Gateway currently stores saved places by internal numeric `places.id`. For the
recommendation service, Gateway must map them to `places.source_id`.

MVP mapping:

- `want_to_go_place_ids`: places in the default saved collection named
  `Want to go`;
- `favourites_place_ids`: all saved places that are not only in `Want to go`,
  or all saved places if no better split exists yet.

This is intentionally simple. A later taste-profile task can introduce explicit
favorite/dislike/want-to-go semantics.

Rules:

- ignore saved places without `source_id`;
- dedupe source ids while preserving stable order;
- cap user input ids before calling recommendation-service if needed;
- include invalid ids from recommendation-service in debug/logs, not in the
  frontend card list.

## Cache Strategy

Do not add Redis in this task.

Add an in-memory TTL cache in the Gateway process for the expensive
recommendation result.

Cache only:

```text
ordered recommendation ids + scores + algorithm metadata
```

Do not cache the fully hydrated card response. Hydration should stay fresh so
`isSaved`, photo urls, and place fields can update independently.

Suggested cache settings:

```text
ttl: 10 minutes
max entries: 500 users/contexts
```

Cache key:

```text
userId
signalHash
limit
excludeInputPlaces
algorithmVersion if known
```

`signalHash` should be based on the sorted/deduped saved source ids and their
collection roles. When the user saves/removes a place, the hash changes and the
next feed request misses cache naturally.

This cache is deliberately process-local:

- it disappears on deploy/restart;
- it is not shared between replicas;
- that is fine for the current single-host MVP;
- move to Redis only when there are multiple Gateway replicas or real pressure.

Frontend also keeps the returned cards in memory for the screen session. Swiping
must not call the backend.

## Fallback Strategy

The feed must not be empty just because personalization is not ready.

Use fallback when:

- request has no valid auth token and no user signals;
- user has no saved source ids;
- recommendation-service is unavailable;
- recommendation-service returns zero hydratable ids.

Fallback source:

- Supabase `places`, ordered by quality/visibility/popularity;
- optional city/country/lat/lng should be soft boosts, not hard filters.

Preferred implementation:

```sql
public.feed_fallback_places(
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
  result_limit integer default 20
)
```

Return the same row shape as personalized hydration.

## Database Access

Add migration:

```text
supabase/migrations/012_feed_places_rpc.sql
```

Add RPC for hydrating recommendation ids:

```sql
public.feed_places_by_source_ids(
  source_ids text[],
  result_limit integer default 20
)
```

Requirements:

- explicit column list;
- join or lateral select the primary photo metadata;
- preserve input order with `array_position(source_ids, p.source_id)`;
- return only fields needed by the feed card;
- do not return raw provider JSON blobs.

Add fallback RPC:

```sql
public.feed_fallback_places(...)
```

Both RPCs should return compatible rows so the mapper can be shared.

## Backend Module Structure

Add a new module:

```text
src/modules/feed/
  index.ts
  feed.module.ts
  controllers/feed.controller.ts
  services/feed.service.ts
  stores/feed.store.ts
  common/feed.schemas.ts
  common/feed.openapi.ts
  common/feed.types.ts
  common/feed.mappers.ts
  tests/
```

Follow the existing layered module style:

```text
controller -> service -> store
```

Responsibilities:

- controller:
  - parse query through zod;
  - optional auth;
  - invalid token -> `401`;
  - log response summary.
- service:
  - collect saved signals;
  - compute cache key;
  - call recommendation-service on cache miss;
  - call fallback when needed;
  - hydrate and enrich `isSaved`;
  - return feed DTO.
- store:
  - query saved signal source ids;
  - hydrate source ids through RPC;
  - fetch fallback feed rows.
- mapper:
  - row -> feed card;
  - recommendation `score` -> integer `matchScore` 0..100;
  - AI/place text -> `blurb`, `whyRecommended`, `tags`.

## Wiring

Update:

- `src/config/routes.ts`
  - `AppRoute.FeedPlaces = "/feed/places"`
  - `VersionedAppRoute.feedPlaces`
- `src/app.ts`
  - register feed module under `API_PREFIX`
- `src/config/swagger.ts`
  - add Feed tag and component schemas
- `src/lib/recommendation-client.ts`
  - add personalized recommendation call
- `docs/tasks/README.md`
  - mark this task
- add frontend handoff doc after implementation:
  - `docs/FRONTEND_FEED_API.md`

## Frontend Handoff Direction

After backend implementation, Swift should:

- add `FeedAPI` / `DecidePlacesStore`;
- call `GET /v1/feed/places?limit=20` once when `DecideScreen` opens;
- keep returned cards in memory while the screen is alive;
- swipe locally without network requests;
- call backend again only on manual refresh or when the screen is reopened after
  a reasonable interval;
- open full details through existing `GET /v1/places/:placeId` if the sheet
  needs more than the feed card payload.

## Observability

Log structured events for:

- feed request completed;
- personalization status;
- cache hit/miss;
- recommendation-service latency;
- recommendation-service failures;
- number of saved signals;
- number of returned recommendation ids;
- number of hydratable places.

Wrap external calls with dependency metrics:

- Supabase saved signal query;
- Supabase feed hydration RPC;
- Supabase fallback RPC;
- recommendation-service personalized request.

## Test Plan

Unit/service tests:

- no auth returns fallback feed;
- invalid auth returns `401`;
- valid auth with saved signals calls recommendation-service;
- saved internal ids are mapped to `source_id`;
- default `Want to go` collection maps to `want_to_go_place_ids`;
- recommendation result order is preserved after hydration;
- missing hydrated ids are skipped;
- cache hit avoids recommendation-service call;
- changed saved signals produce a different cache key;
- recommendation-service error falls back instead of returning `500`;
- empty recommendation response falls back;
- `score` maps to `matchScore` 0..100;
- `isSaved` is enriched for authenticated users.

Route/OpenAPI tests:

- `GET /v1/feed/places` returns feed response;
- query validation rejects invalid `limit`, lone `lat`/`lng`, bad booleans;
- OpenAPI includes `FeedPlacesQuery`, `FeedPlaceCard`, `FeedPlacesResponse`;
- existing map/search/place routes are unchanged.

Manual checks:

```bash
curl "https://sloco.pp.ua/v1/feed/places?limit=20"
curl "https://sloco.pp.ua/v1/feed/places?limit=20" \
  -H "Authorization: Bearer <supabase_access_token>"
```

Docker-network check from the Gateway container:

```bash
node -e "fetch('http://recommendation-service:8000/v1/health/ready').then(r=>r.json()).then(console.log)"
```

Run:

```bash
pnpm typecheck
pnpm test
pnpm lint
pnpm build
```

## Out Of Scope

- Redis or distributed cache.
- Infinite feed cursor/pagination.
- Like/dislike feedback loop.
- Route/map integration.
- Training or rebuilding embeddings.
- Admin tools for algorithm tuning.
- Recommendation-service API changes unless the current contract is insufficient.

## Assumptions

- Recommendation service is private and reachable from Gateway through
  `RECOMMENDATION_SERVICE_URL`.
- Recommendation service returns ranked external `place_id` values that map to
  Gateway `places.source_id`.
- Gateway remains the only public backend API for iOS.
- The first implementation is optimized for a single Gateway process on the
  current Hetzner Docker Compose setup.
- If the product later runs multiple Gateway replicas, the process-local cache
  should move to Redis or another shared cache.
