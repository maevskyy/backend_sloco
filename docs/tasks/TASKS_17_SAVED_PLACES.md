# TASKS 17: Saved Places + Collections Frontend Contract

## Summary

Rework the current saved places foundation into the contract requested by the
iOS Saved tab.

Status: implemented.

The first implementation is useful, but too low-level:

- it has a global `saved_places` table;
- it supports save / unsave / list saved places;
- it adds `isSaved` to map places.

The frontend needs a richer product surface:

- a Saved dashboard;
- user collections;
- recently saved places;
- collection detail view;
- collection membership mutations;
- saved collection ids on map / place responses.

So this task keeps the good foundation and changes the API shape to match the
real frontend contract.

## Current Backend State

Already implemented locally:

- migration:
  - `supabase/migrations/005_create_saved_places.sql`
- module:
  - `src/modules/saved-places/`
- endpoints:
  - `POST /v1/places/:placeId/save`
  - `DELETE /v1/places/:placeId/save`
  - `GET /v1/me/saved-places`
- map enrichment:
  - anonymous map requests return `isSaved: false`;
  - authenticated map requests return real `isSaved`;
  - invalid auth returns `401`.

This should be treated as a foundation, not the final frontend contract.

## Frontend Requirement Source

Read and follow:

```text
frontend/SAVED_BACKEND_REQUIREMENTS.md
```

Main desired endpoints:

- `GET /v1/me/saved`
- `GET /v1/me/saved/collections/:collectionId`
- `POST /v1/me/saved/places`
- `DELETE /v1/me/saved/places/:placeId`
- `POST /v1/me/saved/collections`
- `PATCH /v1/me/saved/collections/:collectionId`
- `DELETE /v1/me/saved/collections/:collectionId`
- `POST /v1/me/saved/collections/:collectionId/places`
- `DELETE /v1/me/saved/collections/:collectionId/places/:placeId`
- `PATCH /v1/me/saved/collections/:collectionId/places/order`

## Product Decisions

### Saved Place Model

Use backend `places.id` as the canonical place id.

Keep provider metadata in responses:

- `source`
- `sourceId`

Do not use TripAdvisor ids as the primary app id.

### Global Saved vs Collections

Use both:

- `saved_places` means the user saved this place globally;
- `saved_collection_places` means the place belongs to a specific collection.

Unsave behavior:

- deleting a saved place removes it from the global saved list;
- deleting a saved place also removes it from all user collections.

### Default Collection

Create a default collection per user:

```text
Want to go
```

Recommended behavior:

- create it lazily on first `GET /v1/me/saved` or first save;
- if `POST /v1/me/saved/places` has no `collectionIds`, add the place to the
  default collection;
- return `isDefault: true`.

Collection ids should be backend-generated UUID strings. The frontend should
not depend on a hardcoded id like `want-to-go`.

### Categories

Frontend expects:

```ts
"food" | "cafe" | "bar" | "nature" | "culture" | "music" | "other"
```

Backend should normalize current place data into this enum.

MVP mapping can be simple:

- cuisine / restaurant / food-like data -> `food`;
- coffee / cafe-like data -> `cafe`;
- bar / pub-like data -> `bar`;
- museum / gallery / art-like data -> `culture`;
- live music / club-like data -> `music`;
- park / walk-like data -> `nature`;
- fallback -> `other`.

`categoryLabel` can be a human label derived from the enum.

### Price Level

Frontend wants numeric:

```ts
0 | 1 | 2 | 3 | 4 | null
```

Backend currently has mixed price data. Convert best-effort:

- `$` -> `1`
- `$$ - $$$` -> `2`
- `$$$$` -> `4`
- unknown -> `null`

### Images and Distance

For this task:

- `imageUrl`: return `null`;
- `distanceText`: return `null`;
- `lastViewedAt`: return `null`.

Do not block saved places on photo storage or user-location distance math.

## Database Schema

Keep `saved_places` as the global saved table.

Add collections.

If migration `005_create_saved_places.sql` has not been applied to Supabase yet,
we can fold this schema into `005`. If it has already been applied, add a new
migration:

```text
supabase/migrations/006_create_saved_collections.sql
```

### saved_places

Existing table remains:

```sql
create table if not exists public.saved_places (
  user_id uuid not null references auth.users(id) on delete cascade,
  place_id bigint not null references public.places(id) on delete cascade,
  created_at timestamptz not null default now(),

  primary key (user_id, place_id)
);
```

Optional extension in this task:

```sql
alter table public.saved_places
  add column if not exists last_viewed_at timestamptz;
```

It is acceptable to skip this column and return `lastViewedAt: null`.

### saved_collections

```sql
create table if not exists public.saved_collections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  color_hex text,
  is_default boolean not null default false,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (user_id, name)
);

create unique index if not exists saved_collections_one_default_per_user_idx
  on public.saved_collections (user_id)
  where is_default = true;

create index if not exists saved_collections_user_sort_idx
  on public.saved_collections (user_id, sort_order, created_at);

alter table public.saved_collections enable row level security;
```

### saved_collection_places

```sql
create table if not exists public.saved_collection_places (
  collection_id uuid not null references public.saved_collections(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  place_id bigint not null references public.places(id) on delete cascade,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),

  primary key (collection_id, place_id)
);

create index if not exists saved_collection_places_user_place_idx
  on public.saved_collection_places (user_id, place_id);

create index if not exists saved_collection_places_collection_sort_idx
  on public.saved_collection_places (collection_id, sort_order, created_at);

alter table public.saved_collection_places enable row level security;
```

RLS decision:

- keep RLS enabled;
- keep access through backend service role only;
- do not expose direct iOS table access in this task.

## API Contract

All saved endpoints are protected:

```http
Authorization: Bearer <supabase_access_token>
```

### Get Saved Dashboard

```http
GET /v1/me/saved
```

Response:

```json
{
  "summary": {
    "savedPlaceCount": 6,
    "collectionCount": 3,
    "recommendationsUseSavedPlaces": true
  },
  "collections": [],
  "recentlySaved": []
}
```

Rules:

- ensure default collection exists;
- return all user collections ordered by `sortOrder`, then `createdAt`;
- each collection includes up to 3 `previewPlaces`;
- `recentlySaved` returns newest saved places first.

### Get Collection Detail

```http
GET /v1/me/saved/collections/:collectionId
```

Response:

```json
{
  "collection": {},
  "places": [],
  "availableCollections": []
}
```

Rules:

- collection must belong to authenticated user;
- places are ordered by `saved_collection_places.sort_order`, then `created_at`;
- `availableCollections` is compact data for the frontend switcher.

### Save Place

```http
POST /v1/me/saved/places
Content-Type: application/json
```

Body:

```json
{
  "placeId": 101,
  "collectionIds": ["uuid-1", "uuid-2"]
}
```

Response:

```json
{
  "placeId": 101,
  "isSaved": true,
  "collectionIds": ["uuid-1"],
  "savedAt": "2026-05-31T09:30:00.000Z"
}
```

Rules:

- idempotent;
- verify place exists;
- verify all collection ids belong to the user;
- if `collectionIds` is omitted or empty, add to default collection;
- insert into `saved_places`;
- insert collection memberships.

### Unsave Place

```http
DELETE /v1/me/saved/places/:placeId
```

Response:

```json
{
  "placeId": 101,
  "isSaved": false,
  "collectionIds": []
}
```

Rules:

- idempotent;
- remove from `saved_collection_places`;
- remove from `saved_places`;
- do not delete the place itself.

### Create Collection

```http
POST /v1/me/saved/collections
Content-Type: application/json
```

Body:

```json
{
  "name": "Dinner ideas",
  "colorHex": "#f0805f"
}
```

Response:

```json
{
  "collection": {}
}
```

Rules:

- collection name is required;
- color is optional;
- `sortOrder` can default to the next available position.

### Update Collection

```http
PATCH /v1/me/saved/collections/:collectionId
Content-Type: application/json
```

Body:

```json
{
  "name": "Dinner ideas",
  "colorHex": "#f0805f",
  "sortOrder": 2
}
```

Rules:

- collection must belong to user;
- default collection can be renamed in MVP unless we explicitly lock it;
- update `updated_at`.

### Delete Collection

```http
DELETE /v1/me/saved/collections/:collectionId
```

Response:

```json
{
  "collectionId": "uuid",
  "deleted": true
}
```

Rules:

- collection must belong to user;
- deleting a collection does not unsave places globally;
- do not allow deleting the default collection in MVP.

### Add Place To Collection

```http
POST /v1/me/saved/collections/:collectionId/places
Content-Type: application/json
```

Body:

```json
{
  "placeId": 101
}
```

Rules:

- collection must belong to user;
- place must exist;
- add to global `saved_places` if not already saved;
- add membership idempotently.

### Remove Place From Collection

```http
DELETE /v1/me/saved/collections/:collectionId/places/:placeId
```

Rules:

- collection must belong to user;
- remove membership only;
- keep global saved row.

### Reorder Collection Places

```http
PATCH /v1/me/saved/collections/:collectionId/places/order
Content-Type: application/json
```

Body:

```json
{
  "placeIds": [103, 101, 102]
}
```

Rules:

- collection must belong to user;
- all ids must already be in the collection;
- update `sort_order` in the given order.

## Response Shapes

### SavedPlaceSummary

```ts
type SavedPlaceSummary = {
  id: number;
  source: string;
  sourceId: string;
  name: string;
  city: string;
  country?: string;
  latitude?: number;
  longitude?: number;
  category: "food" | "cafe" | "bar" | "nature" | "culture" | "music" | "other";
  categoryLabel?: string;
  rating?: number | null;
  priceLevel?: 0 | 1 | 2 | 3 | 4 | null;
  tags?: string[];
  distanceText?: string | null;
  imageUrl?: string | null;
  savedAt: string;
  lastViewedAt?: string | null;
};
```

### SavedCollection

```ts
type SavedCollection = {
  id: string;
  name: string;
  colorHex?: string;
  placeCount: number;
  placeIds: number[];
  previewPlaces: SavedPlaceSummary[];
  createdAt: string;
  updatedAt: string;
  isDefault?: boolean;
  sortOrder?: number;
};
```

## Map / Place Integration

Extend authenticated map responses from:

```json
{
  "id": 101,
  "isSaved": true
}
```

to:

```json
{
  "id": 101,
  "isSaved": true,
  "savedCollectionIds": ["uuid-1"]
}
```

Rules:

- public request: `isSaved: false`, `savedCollectionIds: []`;
- authenticated request: real saved state and real collection ids;
- invalid auth: `401`;
- do not query collection state per place.

Implementation strategy:

- fetch map places;
- fetch `saved_places` for all place ids;
- fetch `saved_collection_places` for all place ids;
- map by `place_id`.

## Backend Implementation Plan

### 1. Adjust Migration

Choose one:

- if `005_create_saved_places.sql` was not applied yet, replace it with full
  saved schema;
- if it was applied, keep `005` and add `006_create_saved_collections.sql`.

### 2. Rework Saved Module

Keep module path:

```text
src/modules/saved-places/
```

Update:

- `saved-places.schemas.ts`
- `saved-places.service.ts`
- `saved-places.routes.ts`
- `saved-places.openapi.ts`
- tests.

Service responsibilities:

- ensure default collection;
- build saved dashboard response;
- build collection detail response;
- save / unsave global places;
- create / update / delete collections;
- add / remove / reorder collection places;
- normalize place summaries.

### 3. Replace Old Endpoints

Because frontend has not integrated the old endpoints yet, prefer replacing the
current old contract instead of supporting both.

Remove or deprecate:

- `POST /v1/places/:placeId/save`
- `DELETE /v1/places/:placeId/save`
- `GET /v1/me/saved-places`

Canonical endpoints become:

- `POST /v1/me/saved/places`
- `DELETE /v1/me/saved/places/:placeId`
- `GET /v1/me/saved`

### 4. Update Map Saved State

Add `savedCollectionIds` to map place schema and service response.

### 5. Update OpenAPI

Swagger should document the frontend contract exactly:

- saved dashboard;
- collection detail;
- mutations;
- auth errors;
- not found errors;
- map `savedCollectionIds`.

### 6. Update Docs

Update:

- `docs/FRONTEND_MAP_API.md`
- `docs/CURRENT_STATE.md`
- `docs/DECISIONS.md`
- `README.md`
- `AGENTS.md`

Add a dedicated API doc if useful:

```text
docs/api/SAVED_API.md
```

## Test Plan

Route tests:

- `GET /v1/me/saved` without token returns `401`.
- `GET /v1/me/saved` creates / returns default collection.
- saved dashboard returns `summary`, `collections`, `recentlySaved`.
- collection preview includes up to 3 places.
- `GET /v1/me/saved/collections/:id` returns collection detail.
- collection detail rejects another user's collection.
- `POST /v1/me/saved/places` saves a place.
- saving without `collectionIds` adds place to default collection.
- saving with `collectionIds` adds memberships.
- saving with another user's collection id returns `404` or `403`.
- saving missing place returns `404`.
- `DELETE /v1/me/saved/places/:placeId` removes global saved and memberships.
- creating a collection returns a collection.
- updating a collection updates name / color / sort order.
- deleting non-default collection removes collection but keeps global saved rows.
- deleting default collection is rejected.
- adding place to collection is idempotent.
- removing place from collection keeps global saved row.
- reordering collection places updates order.

Map tests:

- anonymous map response has `isSaved: false` and `savedCollectionIds: []`.
- authenticated map response includes `isSaved: true`.
- authenticated map response includes `savedCollectionIds`.
- invalid auth still returns `401`.

Swagger tests:

- saved dashboard path exists;
- collection paths exist;
- mutation paths exist;
- schemas exist;
- map schema includes `savedCollectionIds`.

Local checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual checks:

```bash
curl -H "Authorization: Bearer <token>" \
  http://65.108.142.55/v1/me/saved

curl -X POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"placeId":101}' \
  http://65.108.142.55/v1/me/saved/places

curl -H "Authorization: Bearer <token>" \
  "http://65.108.142.55/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
```

## Observability

Log compact product events:

```json
{
  "eventType": "saved_place",
  "action": "save",
  "placeId": 101,
  "collectionCount": 1
}
```

```json
{
  "eventType": "saved_collection",
  "action": "create",
  "collectionId": "uuid"
}
```

Do not log full saved arrays or full place objects.

## Out Of Scope

Do not add in this task:

- place notes;
- collection sharing;
- collaborative lists;
- photo storage;
- distance calculation from user location;
- recommendation model retraining;
- separate analytics/event ingestion;
- direct iOS Supabase table access policies.

## Assumptions

- Supabase Auth foundation already exists.
- `public.places` already exists.
- Backend uses service role key and authorizes by verified Supabase user id.
- Frontend can send Supabase access tokens to backend.
- Saved data is private per user.
- UUID collection ids are acceptable for iOS.
- MVP can return `imageUrl`, `distanceText`, and `lastViewedAt` as `null`.
