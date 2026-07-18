# Frontend Feed API

Use this endpoint for the iOS `Decide for me` screen.

Swagger remains the source of truth:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

## Endpoint

```http
GET /v1/feed/places
Authorization: Bearer <supabase_access_token> optional
```

Production:

```text
https://sloco.pp.ua/v1/feed/places
```

Query params:

```text
limit    optional, default 20, max 50
offset   optional, default 0, min 0
lat      optional, send together with lng
lng      optional, send together with lat
city     optional context boost
country  optional context boost
debug    optional "true" | "false", default "false"
```

## How The Screen Should Use It

Call once when `DecideScreen` opens:

```http
GET https://sloco.pp.ua/v1/feed/places?limit=20&offset=0
```

If the user is logged in, attach the Supabase Auth access token:

```http
Authorization: Bearer <session.access_token>
```

Keep the returned cards in the screen/store memory. Swiping between cards should
not call the backend. Refresh only when the user explicitly asks for new picks,
or when reopening the screen after a reasonable interval.

For pagination, keep the same query context and advance only `offset`:

```http
GET https://sloco.pp.ua/v1/feed/places?limit=30&offset=60
```

The backend serves pages from one cached ranked snapshot per user/context, so
page 2 keeps the same ordering as page 1 instead of reshuffling. `rank` is
global within that snapshot, not local to the page.

Stop paging when the backend returns fewer than `limit` cards. `offset` at or
beyond the end returns `places: []`. Today the snapshot depth is about 100 cards
per refresh cycle.

## Response Shape

```json
{
  "feed": {
    "personalizationStatus": "personalized",
    "cacheStatus": "miss",
    "algorithmVersion": "embedding_recommender_v1",
    "embeddingRunId": "20260531T173837Z",
    "generatedAt": "2026-06-01T10:00:00.000Z",
    "expiresAt": "2026-06-01T10:10:00.000Z"
  },
  "inputSummary": {
    "favouritesCount": 8,
    "wantToGoCount": 3,
    "validInputCount": 10,
    "invalidPlaceIds": []
  },
  "places": [
    {
      "id": 123,
      "source": "google",
      "sourceId": "ChIJ...",
      "name": "Origo Coffee",
      "country": "RO",
      "city": "Bucharest",
      "category": "cafe",
      "primaryType": "coffee_shop",
      "latitude": 44.43,
      "longitude": 26.1,
      "rating": 4.7,
      "priceLevel": 2,
      "numberOfReviews": 120,
      "mapVisibilityScore": 91,
      "matchScore": 94,
      "rank": 1,
      "whyRecommended": "Because this matches places you saved.",
      "blurb": "Specialty coffee spot with a calm work-friendly vibe.",
      "tags": ["quiet", "specialty coffee"],
      "distanceMeters": null,
      "primaryPhoto": null,
      "isSaved": false,
      "reaction": null
    }
  ]
}
```

## Personalization Status

```text
personalized
anonymous_fallback
no_signals_fallback
empty_recommendation_fallback
recommendation_service_fallback
```

The screen should render all statuses. Fallback statuses are not errors; they
mean the backend returned good generic picks while personalization is unavailable
or warming up.

## Opening Details

The feed card is enough for the Decide card UI. If the full sheet needs richer
fields, open details through:

```http
GET /v1/places/:placeId
```

Do not call the private recommendation service from iOS.

## User Reactions

Authenticated clients can persist one mutually-exclusive reaction per place:
`favorite`, `dislike`, or `hide`.

```http
PUT /v1/me/places/:placeId/reaction
Authorization: Bearer <supabase_access_token>
Content-Type: application/json
```

Body:

```json
{
  "reaction": "favorite"
}
```

Response:

```json
{
  "placeId": 123,
  "reaction": "favorite"
}
```

Delete a reaction:

```http
DELETE /v1/me/places/:placeId/reaction
Authorization: Bearer <supabase_access_token>
```

Response:

```http
204 No Content
```

Read all reactions:

```http
GET /v1/me/reactions
Authorization: Bearer <supabase_access_token>
```

Response:

```json
{
  "favorites": [123, 456],
  "dislikes": [789],
  "hidden": [321]
}
```
