# backend_sloco

Backend API for a taste-based city discovery MVP.

## Stack

- Node.js
- TypeScript
- Fastify
- Supabase Postgres
- Zod
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
GET /health
GET /health/supabase
```

Production:

```bash
curl http://52.18.13.69/health
curl http://52.18.13.69/health/supabase
```

## Current Map API

```http
GET /map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100
```

Frontend handoff docs:

```text
docs/FRONTEND_MAP_API.md
```

## Docs

Start here:

```text
docs/README.md
```

Agent/contributor guide:

```text
AGENTS.md
```
