# Frontend Reviews API

The signed-in user's own place reviews: Profile → My ratings & reviews and the
place sheet (Leave a review / Edit your review). Answers the iOS spec
`USER_REVIEWS_API.md` (2026-10-01), stage 1 — **without photos** (SLO-69).
Photo upload (`POST /v1/me/review-photos`) is SLO-70.

Swagger remains the source of truth:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

All three endpoints need `Authorization: Bearer <Supabase access token>`;
without a valid one they answer `401`.

## The model

- At most **one review per place** per user. A second `PUT` to the same place
  replaces the first one. The client identifies a review by its `placeId`.
- `rating` — integer 1…5, required.
- `text` — optional, empty string allowed, at most 2000 characters.
- `tags` — any subset of `romantic`, `trendy`, `lively`, `quiet`, `social`,
  `relaxed`, `cozy`. Repeats are dropped; the order you send is kept.
- `photos` — always `[]` for now. `photoIds` must be empty or omitted (see errors).
- `helpfulCount` — always `0`; there is no "mark helpful" action yet.

## `GET /v1/me/reviews`

Newest first (`createdAt` descending). Query: `limit` 1…100 (default 50),
`offset` ≥ 0 (default 0).

```json
{
  "reviews": [ReviewDTO],
  "total": 19
}
```

`total` counts all of the user's reviews, not just this page. A new account
gets `{"reviews": [], "total": 0}`.

## `PUT /v1/me/places/{placeId}/review`

Creates the review or replaces it. Answers `200 { "review": ReviewDTO }` in both
cases.

```json
{
  "rating": 4,
  "text": "Great flat white, tiny place.",
  "tags": ["cozy"],
  "photoIds": []
}
```

A replace keeps `createdAt` and `helpfulCount` and moves `updatedAt`. On create
`createdAt == updatedAt`.

## `DELETE /v1/me/places/{placeId}/review`

`204`. Deleting a review that does not exist is also `204`.

## ReviewDTO

```json
{
  "placeId": 6124,
  "place": {
    "id": 6124,
    "name": "Random Space",
    "rating": 4.7,
    "numberOfReviews": 160,
    "primaryPhoto": {
      "path": "sloco_ai/2000566879812928176/00_vibe.jpg",
      "url": "https://pub-….r2.dev/sloco_ai/2000566879812928176/00_vibe.jpg",
      "width": 1080,
      "height": 1440,
      "source": "vibe"
    }
  },
  "rating": 5,
  "text": "A really nice place!",
  "tags": ["romantic", "trendy"],
  "photos": [],
  "helpfulCount": 0,
  "createdAt": "2026-10-06T10:00:00.130625+00:00",
  "updatedAt": "2026-10-06T10:05:12.153043+00:00"
}
```

- `place` uses the same field names and shapes as `GET /v1/places/{id}`
  (`primaryPhoto` is the same object, `null` when the place has no photo), so
  the list draws every card without a details request per review.
- Timestamps are in the same format as the other endpoints (`saved`, `reactions`):
  ISO 8601 with fractional seconds and a `+00:00` offset.
- If a place leaves the catalog, its review disappears from the list and from
  `total`.

## Errors

| status | when |
|---|---|
| `401` | no or invalid session |
| `404` | `placeId` is not a place (`PUT` and `DELETE`) |
| `422` | `rating` outside 1…5 or not an integer, `text` over 2000, an unknown tag, more than 10 `photoIds`, any `photoIds` entry (no photo can be uploaded yet, so every id is "not uploaded by this user"), `limit`/`offset` out of range |

`422` body: `{"status": "error", "message": "Invalid review request", "issues": [...]}`.
