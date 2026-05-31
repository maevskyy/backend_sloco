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
GET /v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
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
