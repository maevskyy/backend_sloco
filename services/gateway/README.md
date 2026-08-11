# backend_sloco

Backend API for a taste-based city discovery MVP.

## Stack

- Node.js
- TypeScript
- Fastify
- Supabase Postgres
- Zod
- Swagger/OpenAPI
- Docker

## Local Development

Install dependencies:

```bash
pnpm install
```

Start the development server:

```bash
pnpm dev
```

## Common Commands

```bash
pnpm build
pnpm test
pnpm lint
pnpm typecheck
```

## Environment

Create a local `.env` from:

```text
.env.example
```

Important variables:

```text
NODE_ENV
HOST
PORT
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

Never commit real secrets.

## Health Checks

```http
GET /v1/health
GET /v1/health/supabase
```

Production:

```bash
curl https://sloco.pp.ua/v1/health
curl https://sloco.pp.ua/v1/health/supabase
```

## API Contract

```http
GET /v1/swagger/docs
GET /v1/swagger/openapi.json
```

Frontend agents should use `/v1/swagger/openapi.json` as the source of truth.

## Current Map API

```http
GET /v1/map/config
GET /v1/map/tiles/:z/:x/:y.mvt?v=2
GET /v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
GET /v1/places/:placeId
```

The iOS map renders from the vector tiles; `/v1/map/places` remains the bbox JSON
endpoint. Tiles are capped per tile by `mapVisibilityScore` and versioned by
`MAP_TILE_VERSION` (`docs/FRONTEND_MAP_VECTOR_TILES.md`).

## Current Search API

```http
GET /v1/search/places?q=coffee&lat=44.43&lng=26.10&radiusMeters=20000
GET /v1/search/places?category=cafe&lat=44.43&lng=26.10&radiusMeters=1500
```

Two modes: text (`q`) and category browse (`category` without `q`, for the chips), with an
optional hard `radiusMeters` cut. `docs/FRONTEND_SEARCH_API.md`.

## Current Feed API

```http
GET /v1/feed/places?limit=20&offset=0&lat=44.43&lng=26.10&sort=distance&category=bar
```

Ranked snapshot 200 deep, personalized for users with signals.
`docs/FRONTEND_FEED_API.md`.

## Current Onboarding API

```http
POST /v1/onboarding/complete
```

Saves the onboarding picks as favourites and records `profiles.onboarding_status`.
`docs/FRONTEND_ONBOARDING_API.md`.

## Current Reactions API

```http
GET /v1/me/reactions
PUT /v1/me/places/:placeId/reaction
DELETE /v1/me/places/:placeId/reaction
```

## Current Saved Places API

```http
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
```

These endpoints require:

```http
Authorization: Bearer <supabase_access_token>
```

Frontend handoff docs:

```text
docs/FRONTEND_MAP_API.md
docs/FRONTEND_SEARCH_API.md
```

## Docs

Start here:

```text
docs/CURRENT_STATE.md
docs/README.md
```

Agent/contributor guide:

```text
AGENTS.md
```
