# TASKS 4: First Map Places Endpoint

## Goal

Add the first frontend-facing endpoint for map data.

The iOS app should be able to request restaurants inside the currently visible
map rectangle and render them as pins.

This is the first real product endpoint after health checks.

## Endpoint

```http
GET /map/places
```

Query params:

```text
city=Berlin
swLat=52.4800
swLng=13.3300
neLat=52.5600
neLng=13.4700
limit=100
```

Required:

- `city`
- `swLat`
- `swLng`
- `neLat`
- `neLng`

Optional:

- `limit`, default `100`, max `200`

## Response Shape

Return compact map pins, not full raw rows.

```json
{
  "places": [
    {
      "id": 1,
      "source": "tripadvisor",
      "sourceId": "d5529357",
      "name": "Pane e Vino",
      "city": "Berlin",
      "latitude": 52.552578,
      "longitude": 13.352883,
      "rating": 4,
      "priceRange": "$$ - $$$",
      "numberOfReviews": 17,
      "rawCuisineStyle": "['Italian', 'Pizza']"
    }
  ]
}
```

Do not expose:

- `embedding_text`
- `raw_reviews`
- internal `created_at`

## Data Source

Read from:

```text
public.raw_tripadvisor_restaurants
```

Use Supabase JS client.

Initial query logic:

```text
city = query.city
latitude >= swLat
latitude <= neLat
longitude >= swLng
longitude <= neLng
order by rating desc nulls last
limit query.limit
```

PostGIS is not required for this task because the first table stores temporary
random Berlin coordinates as plain `latitude` / `longitude`.

## Implementation Shape

Add a map module:

```text
src/modules/map/map.routes.ts
src/modules/map/map.schemas.ts
src/modules/map/map.service.ts
src/modules/map/map.routes.test.ts
```

Register it in:

```text
src/app.ts
```

Validation:

- Use `zod`.
- Convert query strings to numbers.
- Reject invalid bounds with `400`.
- Reject `limit > 200` with `400`.

Service:

- Use `getSupabaseClient()`.
- Keep raw Supabase row mapping inside the map module.
- Throw on Supabase errors and return `500`.

## Testing

Add route tests with mocked service/client behavior:

- Returns places for valid bbox query.
- Returns `400` for missing required query params.
- Returns `400` for invalid coordinates.
- Applies default limit.
- Caps/rejects over-limit requests.
- Returns `500` when Supabase query fails.

Local checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual check after Supabase env/table/import:

```bash
curl "http://127.0.0.1:3000/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700"
```

Expected:

```json
{
  "places": [...]
}
```

## Future Follow-Ups

- Add category/cuisine filtering.
- Add normalized `places` table.
- Replace raw lat/lng filter with PostGIS.
- Add personalization score.
- Add `whyRecommended`.
- Add saved state after auth exists.

