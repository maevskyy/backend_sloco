# TASKS 17: Saved Places

## Goal

Add saved places as the first user-owned product feature.

Users should be able to:

- save a place;
- unsave a place;
- get their saved places list;
- see saved state in map/place UI once frontend is authenticated.

This task should be implemented after the Supabase Auth foundation is ready.

## Dependencies

Required:

- `TASKS_16_SUPABASE_AUTH_FOUNDATION.md`
  - backend verifies `Authorization: Bearer <supabase_access_token>`;
  - protected routes can access authenticated `user.id`.

- `TASKS_11_DB_PLACES.md`
  - canonical `public.places` table exists;
  - saved places reference `places.id`, not raw provider tables.

Related:

- `TASKS_14_MAP_BBOX_ONLY.md`
  - map response keeps returning place ids;
  - frontend can match map pins to saved state.

## Database Schema

Add migration:

```text
supabase/migrations/00X_create_saved_places.sql
```

Schema:

```sql
create table if not exists public.saved_places (
  user_id uuid not null references auth.users(id) on delete cascade,
  place_id bigint not null references public.places(id) on delete cascade,
  created_at timestamptz not null default now(),

  primary key (user_id, place_id)
);

create index if not exists saved_places_user_created_at_idx
  on public.saved_places (user_id, created_at desc);

create index if not exists saved_places_place_id_idx
  on public.saved_places (place_id);

alter table public.saved_places enable row level security;
```

RLS decision:

- enable RLS now so the table is not accidentally open later;
- keep access through the backend service role for MVP;
- do not add direct iOS table policies until we intentionally allow frontend
  Supabase table access.

## API Contract

All saved places endpoints are protected.

Auth header:

```http
Authorization: Bearer <supabase_access_token>
```

### Save Place

```http
POST /v1/places/:placeId/save
```

Success:

```json
{
  "placeId": 123,
  "isSaved": true
}
```

Behavior:

- idempotent;
- already saved still returns `200`;
- missing place returns `404`;
- missing/invalid auth returns `401`.

### Unsave Place

```http
DELETE /v1/places/:placeId/save
```

Success:

```json
{
  "placeId": 123,
  "isSaved": false
}
```

Behavior:

- idempotent;
- not saved still returns `200`;
- missing place returns `404`;
- missing/invalid auth returns `401`.

### Get Saved Places

```http
GET /v1/me/saved-places
```

Query params:

```text
limit?   default 50, max 100
cursor?  optional pagination cursor
```

MVP response:

```json
{
  "places": [
    {
      "id": 123,
      "source": "osm",
      "sourceId": "osm:node/123",
      "name": "Quiet Coffee",
      "country": "Romania",
      "city": "Bucharest",
      "category": "cafe",
      "latitude": 44.43,
      "longitude": 26.09,
      "rating": null,
      "priceLevel": null,
      "numberOfReviews": null,
      "savedAt": "2026-05-31T10:00:00.000Z",
      "isSaved": true
    }
  ],
  "nextCursor": null
}
```

Ordering:

```text
newest saved first
```

Pagination:

- implement `limit` first;
- return `nextCursor: null` for MVP if cursor pagination is not needed yet;
- add cursor pagination later when saved lists become large.

## Map Saved State

Map pins should eventually expose `isSaved` so frontend can render heart state.

Recommended behavior:

- `GET /v1/map/places` remains public.
- If no `Authorization` header is sent, return `isSaved: false`.
- If a valid `Authorization` header is sent, return true saved state for the
  authenticated user.
- If an invalid `Authorization` header is sent, return `401` instead of silently
  treating the user as anonymous.

Map pin addition:

```json
{
  "id": 123,
  "name": "Quiet Coffee",
  "isSaved": true
}
```

Implementation rule:

- do not query saved state per place;
- fetch map places first;
- fetch saved rows with `user_id = :userId` and `place_id in (:placeIds)`;
- build a `Set<place_id>` and map `isSaved`.

## Backend Implementation

Add module:

```text
src/modules/saved-places/
  saved-places.routes.ts
  saved-places.schemas.ts
  saved-places.service.ts
  saved-places.openapi.ts
  saved-places.routes.test.ts
```

Register routes in:

```text
src/app.ts
```

Service responsibilities:

- verify place exists in `public.places`;
- insert into `saved_places` idempotently;
- delete from `saved_places` idempotently;
- list saved places joined with `places`;
- map database rows into API response shape.

Route responsibilities:

- require auth;
- parse and validate `placeId`;
- return consistent `401`, `404`, and `200` responses;
- log compact response summaries, not full place arrays.

## OpenAPI / Swagger

Update Swagger:

- add `SavedPlaces` tag;
- document bearer auth;
- document:
  - `POST /v1/places/{placeId}/save`;
  - `DELETE /v1/places/{placeId}/save`;
  - `GET /v1/me/saved-places`;
- add `isSaved` to map place schema if map enrichment is implemented in this
  task.

## Frontend Contract

iOS sends Supabase access token:

```http
Authorization: Bearer <session.access_token>
```

Frontend flows:

- tap heart on map pin or place preview -> `POST /v1/places/:id/save`;
- tap saved heart -> `DELETE /v1/places/:id/save`;
- saved tab / wishlist -> `GET /v1/me/saved-places`;
- authenticated map requests include `Authorization` so pins can include
  `isSaved`.

Optimistic UI:

- frontend can optimistically flip the heart;
- if backend returns error, revert and show a lightweight error.

## Docs To Update

- `docs/FRONTEND_MAP_API.md`
  - add auth header note for authenticated map;
  - add `isSaved` field once implemented.

- Add saved places API docs if needed:

  ```text
  docs/api/SAVED_PLACES_API.md
  ```

- `AGENTS.md`
  - add saved places endpoints to important API section if useful.

## Test Plan

Route tests:

- `POST /v1/places/:placeId/save` without token returns `401`.
- `DELETE /v1/places/:placeId/save` without token returns `401`.
- `GET /v1/me/saved-places` without token returns `401`.
- save existing place returns `{ isSaved: true }`.
- saving same place twice is idempotent.
- save missing place returns `404`.
- unsave existing saved place returns `{ isSaved: false }`.
- unsaving non-saved existing place is idempotent.
- list saved places returns newest first.
- saved places are isolated by `user_id`.

Map saved state tests, if included:

- anonymous map response includes `isSaved: false`.
- authenticated map response includes `isSaved: true` for saved ids.
- invalid token on map request returns `401`.

Local checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual checks:

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  http://65.108.142.55/v1/places/123/save

curl \
  -H "Authorization: Bearer <token>" \
  http://65.108.142.55/v1/me/saved-places

curl -X DELETE \
  -H "Authorization: Bearer <token>" \
  http://65.108.142.55/v1/places/123/save
```

## Observability

Add compact logs:

```json
{
  "eventType": "response",
  "event": "response summary",
  "path": "/v1/places/:placeId/save",
  "placeId": 123,
  "isSaved": true
}
```

Do not log full saved place arrays.

## Out Of Scope

Do not add in this task:

- folders / custom wishlists;
- notes on saved places;
- social sharing;
- collaborative lists;
- push notifications;
- recommendation retraining from saves;
- offline sync conflict handling;
- frontend Supabase table access policies.

## Assumptions

- Supabase Auth foundation exists before this task is implemented.
- `public.places` exists before this task is implemented.
- Backend uses service role key and authorizes by verified user id.
- Frontend stores Supabase session and can send access token to backend.
- Saved places are private per user.
