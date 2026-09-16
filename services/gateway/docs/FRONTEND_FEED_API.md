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
city     optional hard cut on shown places (unaccent / case-insensitive)
country  optional context boost
sort     optional "relevance" | "distance", default "relevance"
category optional one or more of cafe|food|bar|culture|nature|shopping|leisure (CSV or repeated)
debug    optional "true" | "false", default "false"
```

`category` filters **before** ranking, so a filtered feed is still a full snapshot of the
best places of that kind — not "whatever survived a cut". Same vocabulary as
`/v1/search/places`; an unknown value is a **400**. On the personalized path the filter is
applied after the recommender returns, so a filtered personalized feed can be shallower
than 200. `nature` and `shopping` are nearly empty in the current catalog — see
`FRONTEND_SEARCH_API.md`.

`city` is a **hard cut on the shown cards**, not a ranking boost and not a taste-model
input. Seeds and clusters still use likes/saves from every city; only the candidate
list is restricted. Match is `lower(unaccent(place.city))` (the same fold the fallback
RPC uses). Unknown or unmatched names (`Bucuresti` ≠ `Bucharest`) return an **empty
page**, not the unscoped ranking. On the personalized path the cut is applied after
hydration, so a city-filtered personalized feed can be shallower than 200. The
fallback path filters inside `feed_fallback_places` and keeps full depth. City names
come from `GET /v1/cities`.

`sort=distance` re-orders the **same** ranked snapshot by great-circle distance from
`lat`/`lng` (ascending, ties keep relevance order) — it is not a different query, so
personalization, `matchScore` and `whyRecommended` behave exactly as on `relevance`.
It requires `lat`/`lng` (**400** without them), and an unknown `sort` value is a **400**
rather than a silent fallback. The effective value is echoed back as `feed.sort`.

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
global within that snapshot, not local to the page — under `sort=distance` it is
positional in the distance ordering, so `offset` windows continue that ordering.

Stop paging when the backend returns fewer than `limit` cards. `offset` at or
beyond the end returns `places: []`. The snapshot depth is **200 cards** per
refresh cycle (raised from 100 on 2026-08-11).

Note when comparing the two sorts: the **whole snapshot** is the same set of places in
both orderings, but any page shorter than the snapshot is not — the first 50 by
relevance and the first 50 by distance are different windows of the same 200.

## Response Shape

```json
{
  "feed": {
    "personalizationStatus": "personalized",
    "cacheStatus": "miss",
    "algorithmVersion": "embedding_recommender_v1",
    "embeddingRunId": "20260531T173837Z",
    "requestId": "3f0e8a3e-a8a9-4c93-9f3a-1b2c3d4e5f60",
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
      "position": 0,
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

## Telemetry ids: `feed.requestId` + card `position` (2026-08-16)

`feed.requestId` is the recommendation serving id ("receipt number") and
`places[].position` is the card's 0-based position inside that serving's snapshot.
Put BOTH into the `context` of every telemetry event born from a feed card —
that link is what turns telemetry into training data. Rules:

- One `requestId` per snapshot: pages, `sort=` re-orders and `category=` cuts of
  the same snapshot share it; it changes when the snapshot refreshes. Dedupe
  impressions per `(requestId, placeId)`.
- `position` is stable under `sort=distance` / `category=` (it points into the
  snapshot); `rank` stays positional per page — use `rank` for display,
  `position` for telemetry.
- Both are `null` on every fallback feed. Send events with
  `context.request_id: null` then — expected and monitored.

Full intake contract: `FRONTEND_EVENTS_API.md`.

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
