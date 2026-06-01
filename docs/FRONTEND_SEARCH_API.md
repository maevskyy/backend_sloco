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
| `q` | string | yes | Search text, `2..100` chars. |
| `lat` | number | no | User/current map latitude. Must be sent together with `lng`. |
| `lng` | number | no | User/current map longitude. Must be sent together with `lat`. |
| `city` | string | no | Optional city context for soft ranking boost. |
| `country` | string | no | Optional country context for soft ranking boost. |
| `limit` | number | no | Result cap, default `20`, max `50`. |

`lat`/`lng` and `city`/`country` are ranking context, not hard filters. The
backend still searches globally, but nearby / same-city results should rank
higher.

## Example

```bash
curl "https://sloco.pp.ua/v1/search/places?q=coffee&lat=44.43&lng=26.10&city=Bucharest&country=RO&limit=20"
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
