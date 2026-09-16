# Frontend Cities API

Use this endpoint for the iOS Profile city picker.

Swagger remains the source of truth:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

## Endpoint

```http
GET /v1/cities
```

Public — no Bearer token.

```text
https://sloco.pp.ua/v1/cities
```

## Response

```json
{
  "cities": [
    { "name": "Bucharest", "country": "Romania", "placeCount": 8000 },
    { "name": "Tbilisi", "country": "Georgia", "placeCount": 4500 }
  ]
}
```

- `name` is the exact `places.city` / `feed.city` / `GET /v1/feed/places?city=` spelling.
  Send that string back as `city=`. Do not rewrite it (`Bucuresti` is not `Bucharest`).
- Sorted by `placeCount` descending, then `name` ascending.
- Only cities that have at least one place. An empty catalog returns `{ "cities": [] }`.

## How it composes with the feed

`GET /v1/feed/places?city=<name>` is a **hard cut on what is shown**, not a
taste-model input. Likes and saves from other cities still build clusters.
Unknown / unmatched names return an empty page, not the mixed ranking.

See `FRONTEND_FEED_API.md`.
