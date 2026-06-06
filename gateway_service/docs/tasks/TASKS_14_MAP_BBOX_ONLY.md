# TASKS 14: Bbox-Only Map Endpoint (Drop Required `city`)

## Goal

Make the map endpoint purely viewport-driven. The frontend should send only the
visible bounding box (and `zoom`); it should not need to know or send which city
it is in.

## Context

The map is a viewport query: "give me places inside this rectangle." The bbox
already answers that unambiguously, and with PostGIS + the GiST index on `geom`
the spatial query is efficient on its own.

`city` is a leftover from the original single-city TripAdvisor MVP, where data
was Berlin-only and `city` was a natural partition. That context is gone:
multiple sources, multiple cities, real coordinates, PostGIS.

## Problem

`places_in_bbox` currently filters by **both** an exact `city` string **and**
the bbox:

```sql
where city = city_filter
  and geom && st_makeenvelope(:swLng, :swLat, :neLng, :neLat, 4326)
```

This is the source of a real bug: a frontend that hardcodes `city=Berlin` while
panning over Bucharest gets `placesCount: 0`, because Berlin rows are not inside
a Bucharest rectangle and Bucharest rows are not `city = 'Berlin'`.

It is also fragile by data: OSM `city` values are heterogeneous (`București`,
`Măgurele`, ...), so an exact `city = ?` filter silently drops valid rows.

## Decision

- **Remove `city` from the map query entirely.** The endpoint becomes bbox-only
  (`swLat`, `swLng`, `neLat`, `neLng`, `+ optional zoom`, `+ optional limit`).
- `city` stays as an **output field** on each place (already part of the pin).
- Do not add an optional `city` filter. Optional-`city` adds branching for
  almost no value; simpler is more robust.
- If a "current city" label is ever needed for UI/analytics, derive it
  server-side from coordinates later — not as a required input from the frontend.

This removes a whole class of city/bbox mismatch bugs and removes needless work
from the frontend.

## API Contract Changes

Before:

```http
GET /v1/map/places?city=Berlin&swLat=...&swLng=...&neLat=...&neLng=...&zoom=13
```

After:

```http
GET /v1/map/places?swLat=...&swLng=...&neLat=...&neLng=...&zoom=13
```

| Param | Required | Notes |
| --- | --- | --- |
| `swLat` | yes | South-west latitude. |
| `swLng` | yes | South-west longitude. |
| `neLat` | yes | North-east latitude. |
| `neLng` | yes | North-east longitude. |
| `zoom` | no | Map zoom level; density fallback from bbox if omitted. |
| `limit` | no | Optional cap, backend-clamped by density. |

This is a **breaking change**: the frontend must stop sending `city`. Sending it
should be ignored, not rejected (extra query params are already tolerated).

## Database / RPC Changes

New migration: `supabase/migrations/003_places_in_bbox_no_city.sql`.

- Drop the old `places_in_bbox(city_filter text, ...)`.
- Create `places_in_bbox(sw_lat, sw_lng, ne_lat, ne_lng, result_limit)` that
  filters only by `geom && st_makeenvelope(...)`, same return columns as today.

Note: Postgres identifies functions by signature, so the old signature must be
dropped to avoid overload ambiguity.

## Code Changes

- `src/modules/map/map.schemas.ts`: remove `city` from `mapPlacesQuerySchema`.
- `src/modules/map/map.service.ts`: drop `city_filter` from the RPC call; remove
  `city` from `MapRankingContext` (or make it optional), since it is no longer an
  input. Ranking does not use it today.
- `src/modules/map/map.openapi.ts`: remove `city` from `MapPlacesQuery` required
  list and properties; update examples.
- `src/modules/map/map.routes.ts`: drop `city: query.city` from the response
  summary log.
- `src/modules/map/map.ranking.ts`: drop `city` from `MapRankingContext` if it is
  removed from the service context.

The response pin shape is unchanged (`city` still returned per place).

## Docs To Update

- `docs/FRONTEND_MAP_API.md`: remove `city` from required params, examples, and
  the Swift request example.
- `AGENTS.md`: the "Current Runtime" / "Important API" curl examples include
  `city=Berlin`; update them to bbox-only (commands change).

## Test Plan

- `map.routes.test.ts`:
  - update `validQuery` to bbox-only (no `city`);
  - a query without `city` returns `200`;
  - missing a required bbox param still returns `400`;
  - invalid bbox ordering still returns `400`.
- `map.ranking.test.ts`: drop `city` from the test `MapRankingContext` if the
  type changes.
- Manual: same bbox over Bucharest now returns data without any `city` param.
- `pnpm build && pnpm test && pnpm lint`.

## Assumptions

- Data is already imported into `public.places` (TASKS 11/12).
- Density limiting (TASKS 13) keeps low-zoom, large-bbox responses readable, so
  dropping the city partition does not cause marker explosion.
- Frontend is updated in lockstep to stop sending `city`.

## Future Follow-Ups

- Optional server-side "current city/area" derivation from coordinates for UI
  labels or analytics.
- Category / taste filters as the next query dimension (not city).
