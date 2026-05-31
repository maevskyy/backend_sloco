# Current State

This is the first context file to read. It summarizes the current truth of the
backend without the historical task-plan noise.

## Runtime

```text
Production API: http://65.108.142.55
Runtime host: Hetzner Ubuntu server
App runtime: Docker Compose + Nginx
Deploy: GitHub Actions -> GHCR -> SSH -> docker compose
Database: Supabase managed Postgres
Observability: Grafana Cloud + Alloy on the server
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

Data mapper commands:

```bash
pnpm map:tripadvisor dumps/raw_tripadvisor_restaurants_import.csv --out dumps/tripadvisor_places.csv
pnpm map:osm dumps/bucharest_cafes.csv --out dumps/osm_bucharest_places.csv
```

Production smoke checks:

```bash
curl http://65.108.142.55/v1/health
curl http://65.108.142.55/v1/health/supabase
curl "http://65.108.142.55/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

## Active API

```http
GET /v1/health
GET /v1/health/supabase
GET /v1/me
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
GET /v1/swagger/docs
GET /v1/swagger/openapi.json
```

OpenAPI is the contract source of truth for frontend agents:

```text
http://65.108.142.55/v1/swagger/openapi.json
```

Human docs for the map endpoint:

```text
docs/FRONTEND_MAP_API.md
```

## Active Database

```text
Serving table: public.places
Auth profile table: public.profiles
Saved places table: public.saved_places
Saved collections table: public.saved_collections
Saved collection membership table: public.saved_collection_places
Geo RPC: public.places_in_bbox(sw_lat, sw_lng, ne_lat, ne_lng, result_limit)
Migrations: supabase/migrations/
```

Map reads are bbox-only. The frontend does not send `city`; `city` is returned
as a place attribute only.

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
TripAdvisor -> scripts/integrations/tripadvisor/map.ts
OpenStreetMap -> scripts/integrations/osm/map.ts
```

## Current Priorities

- Keep backend deploy simple: one Hetzner host, Docker Compose, managed
  Supabase.
- Keep map API lightweight: bbox query, density/ranking, no heavy place details.
- Build the next user-owned product layers: taste profile, favorite place input,
  and personalization on top of saved places.
- Keep task docs as history/plans; prefer this file for current context.
- Do not self-host Postgres during MVP unless there is a real business or cost
  reason.
- Treat self-hosted Grafana/Loki/Prometheus as TBD, not current runtime.
