# Frontend Search API

This document describes the iOS place search endpoint.

Swagger/OpenAPI remains the source of truth:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

## Endpoint

```http
GET /v1/search/places
```

The endpoint searches across all backend places, not only the current map bbox.
Use it for search bars and search result lists.

It is public, but supports optional auth:

```http
Authorization: Bearer <supabase_access_token>
```

If auth is omitted, every result returns `isSaved: false`. If auth is valid,
`isSaved` reflects saved state. Invalid auth returns `401`.

## Query Parameters

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `q` | string | see below | Search text, `2..100` chars. |
| `category` | enum list | see below | One or more coarse buckets, CSV or repeated. Values: `cafe`, `food`, `bar`, `culture`, `nature`, `shopping`, `leisure`. Unknown value → `400`. |
| `radiusMeters` | number | no | **Hard** distance cut around `lat`/`lng`, `1..50000`. Ignored without coordinates. |
| `lat` | number | no | User/current map latitude. Must be sent together with `lng`. |
| `lng` | number | no | User/current map longitude. Must be sent together with `lat`. |
| `city` | string | no | Optional city context for soft ranking boost. |
| `country` | string | no | Optional country context for soft ranking boost. |
| `limit` | number | no | Result cap, default `20`, max `50`. |

**At least one of `q` / `category` is required** (neither → `400`).

`city`/`country` are ranking context, not filters. `lat`/`lng` alone are also only
context — the backend searches globally, so **send `radiusMeters` if you do not want
other cities in the list**. Measured: `?q=coffee` from Bucharest returns 4 of 10 results
in Tbilisi; `?q=coffee&radiusMeters=20000` returns 0.

### Two modes

- **Text** (`q` present) — ranked by relevance as before. `category`/`radiusMeters` narrow
  it if sent.
- **Browse** (`category` without `q`) — for category chips. No text ranking: results are
  the nearest places of that kind when `lat`/`lng` are given, otherwise the most visible
  ones. `matchReason` is `category`, and `query` echoes `""`.

⚠️ **Text search matches names, not intent.** `q=coffee` finds places *named* "coffee",
not cafés in general (1 of 5 real nearby cafés was found in a live check), and Cyrillic
queries return nothing. Use `category=cafe` for "show me cafés". Tracked as
`docs/tasks/TASKS_49_SEARCH_INTENT.md`.

### Bucket coverage (current two-city catalog)

`cafe`, `food`, `bar`, `culture`, `leisure` are well populated. **`nature` and `shopping`
are nearly empty** — the catalog is food + things-to-do, so there are no parks or malls in
it (Bucharest centre: 0 nature, 1 shopping within 5 km). The buckets exist so the contract
stays stable; consider hiding those two chips until the catalog grows.

## Example

```bash
# text search, kept local
curl "https://sloco.pp.ua/v1/search/places?q=coffee&lat=44.43&lng=26.10&radiusMeters=20000&limit=20"

# category chip: cafés within 1.5 km, nearest first
curl "https://sloco.pp.ua/v1/search/places?category=cafe&lat=44.43&lng=26.10&radiusMeters=1500&limit=20"
```

## Response

```json
{
  "query": "coffee",
  "places": [
    {
      "id": 123,
      "name": "Origo Coffee",
      "category": "cafe",
      "primaryType": "cafe",
      "city": "Bucharest",
      "country": "RO",
      "formattedAddress": "Bucharest, Romania",
      "latitude": 44.437,
      "longitude": 26.101,
      "rating": 4.7,
      "priceLevel": 2,
      "primaryPhoto": null,
      "distanceMeters": 830,
      "matchReason": "name",
      "isSaved": false
    }
  ]
}
```

When a user selects a result, request full details with:

```http
GET /v1/places/:placeId
```

## Frontend Behavior

- Debounce search input around `250-400ms`.
- Do not search for less than 2 chars.
- Send current map/user location when available.
- Do not client-filter to the current map bbox.
- Render the result list from `/v1/search/places`.
- Open full place details through `/v1/places/:placeId`.
