# TASKS 13: Map Density And Ranking

## Goal

Stop returning a naive list of all places inside the viewport. The backend
should decide **which places are worth showing on the current map view**, and the
frontend should only render what it receives.

This is the first step toward a Google Maps-like map experience, but adapted to
our product: not "show every POI", but "show the best taste-based discovery
candidates for this user and zoom level".

## Problem

Current map API:

```http
GET /v1/map/places?city=Berlin&swLat=...&swLng=...&neLat=...&neLng=...&limit=100
```

Current behavior:

- frontend sends viewport bbox;
- backend returns up to `limit` places inside bbox;
- sorting is mostly database-level, currently rating-first;
- there is no density control;
- a small visible area can still contain too many points;
- a large visible area can return random-looking results.

This creates bad UX:

- too many markers;
- visual overlap;
- frontend has to render noisy data;
- the map feels less curated and less "smart";
- future personalization has no clear insertion point.

## Core Product Decision

The frontend should not solve place selection.

Frontend responsibilities:

- send current viewport;
- send zoom;
- optionally send filters;
- render returned places/clusters;
- react to taps.

Backend responsibilities:

- decide max density for the current zoom;
- rank candidate places;
- return only the best places for this viewport;
- later use taste/user score in the ranking;
- optionally return clusters when there are too many places.

## How Map Products Usually Think About This

We do not know the exact Google Maps internals, but the common architecture is:

1. The frontend sends the visible map area and zoom.
2. Backend/search service finds candidates inside that area.
3. Candidates are scored by relevance/prominence/context.
4. Results are density-limited so the screen is readable.
5. At low zoom, the system returns clusters/aggregates or only very prominent
   places.
6. At high zoom, it returns more individual places.

The important idea: **map markers are a ranked presentation layer, not a raw
database dump**.

## MVP Strategy

Do not build complex clustering yet.

For this task, add a simple but extensible backend ranking pipeline:

```text
bbox query -> candidate rows -> score rows -> density limit -> API response
```

The scoring function should exist as a separate internal function now, even if
it is simple. Later we can replace or extend it with personalization.

## API Contract Changes

Add **optional** query param:

```http
zoom=13
```

`zoom` is optional, not required. Reasons:

- making it required breaks the current frontend contract (today's endpoint has
  no `zoom`);
- Swift map frameworks expose zoom only indirectly (camera altitude / region
  span), so the value the frontend sends can be noisy;
- the backend already receives the bbox, so when `zoom` is absent it can derive
  an effective density from the visible bbox span instead.

Behavior:

- `zoom` present and valid → use the zoom-based density bucket;
- `zoom` absent → derive density from the bbox span (fallback);
- `zoom` present but out of range → `400`.

Full endpoint:

```http
GET /v1/map/places
  ?city=Bucharest
  &swLat=44.403
  &swLng=26.049
  &neLat=44.468
  &neLng=26.150
  &zoom=13
```

Keep `limit` optional, but backend should clamp it against zoom-based density.

Recommended query params:

| Param | Required | Notes |
| --- | --- | --- |
| `city` | yes | Current city filter. |
| `swLat` | yes | South-west latitude. |
| `swLng` | yes | South-west longitude. |
| `neLat` | yes | North-east latitude. |
| `neLng` | yes | North-east longitude. |
| `zoom` | no | Frontend map zoom level. If omitted, density is derived from the bbox span. |
| `limit` | no | User/debug cap, still backend-clamped. |

## Zoom-Based Density

Add a small config function:

```ts
getMapDensityLimit(zoom: number): number
```

Initial MVP values:

| Zoom | Meaning | Max individual places |
| --- | --- | --- |
| `< 11` | city/area overview | 8 |
| `11..12` | district level | 15 |
| `13..14` | neighborhood level | 25 |
| `>= 15` | street/detail level | 40 |

These are intentionally conservative. Reasons:

- an iPhone screen comfortably holds ~10-25 non-overlapping pins; 60 reads as a
  cluttered "POI dump";
- we deliberately do **not** ship clustering in this task, so the density cap is
  the **only** declutter mechanism — it must stay low;
- the product is a curated taste-discovery assistant, not a Google Maps clone,
  so "fewer, better pins" matches the positioning.

The binding zoom in practice is `13..14` (neighborhood), where the cap actually
triggers. At `>= 15` the visible area is tiny, so `40` rarely binds.

This is not "perfect", it is a sane product default. We can tune after seeing
the iOS map.

> Future-robust model: density is really "pins per visible area", not per zoom
> bucket. The backend already receives the bbox, so it can derive visible span
> directly. Zoom buckets are a fine MVP proxy; an area-based limit is the next
> iteration.

Effective limit:

```ts
effectiveLimit = min(userLimit ?? densityLimit, densityLimit)
```

## Candidate Overfetch

To rank well, do not fetch exactly `effectiveLimit` from the DB.

Fetch more candidates first:

```ts
candidateLimit = min(effectiveLimit * 4, 400)
```

Then score and slice in backend:

```ts
places = scoredCandidates.slice(0, effectiveLimit)
```

Why:

- if DB returns only 20 rows, backend cannot choose the best 20;
- overfetch gives the scoring function room to work;
- `400` is still tiny for MVP.

## MVP Scoring

Add internal scoring function:

```ts
scoreMapPlace(row, context): number
```

Initial non-personalized scoring:

```text
score =
  rating score
  + reviews count score
  + source quality weight
  + tiny deterministic jitter
```

Suggested first version:

| Signal | Rule |
| --- | --- |
| `rating` | `rating * 10`, else `0` |
| `reviews_count` | `min(log10(reviews_count + 1) * 5, 20)`, else `0` |
| `source` | TripAdvisor `+5`, OSM `+0` for now |
| deterministic jitter | small stable value from `source_id`, e.g. `0..1` |

The deterministic jitter prevents identical scores from always sorting by DB id,
but keeps results stable between requests.

> Caveat for OSM-only cities: OSM data has no `rating` / `reviews_count`, so for
> a city like Bucharest every place scores ~0 and ranking collapses to jitter
> (effectively "N random places"). Where the source has no quality signal, the
> perceived quality of the selection comes from **spatial spread + a low density
> cap**, not from the score. This is another reason to keep the caps low and to
> pull simple viewport diversification (see Future Follow-Ups) forward sooner
> than it looks.

Later personalization can be inserted as:

```text
score += userTasteScore * 100
```

## Important Future Personalization Hook

The function signature should accept context even if unused today:

```ts
type MapRankingContext = {
  zoom: number;
  city: string;
  userId?: string;
};
```

Future flow:

```text
candidate place -> model/user taste score -> final score -> density slice
```

This means TASKS 13 creates the slot where Python recommendation service can
later plug in.

## Clustering Decision

Do not implement clustering in this task.

Reason:

- we have small data volume;
- frontend currently needs pins, not aggregate cluster UX;
- ranking + density limit solves the immediate marker explosion;
- clustering has a separate API shape and should be planned only when needed.

Future cluster response may look like:

```json
{
  "type": "cluster",
  "latitude": 44.43,
  "longitude": 26.10,
  "count": 12
}
```

But not now.

## Backend Changes

- Update map query schema:
  - add optional `zoom`;
  - validate range `1..22` only when present;
  - when `zoom` is absent, derive an effective density from the bbox span.

- Update OpenAPI:
  - document `zoom`;
  - document backend density behavior.

- Update map service:
  - calculate `densityLimit`;
  - calculate `candidateLimit`;
  - call `places_in_bbox` with `candidateLimit`;
  - score candidates in TypeScript;
  - sort by score descending;
  - return only `effectiveLimit`.

- Add ranking module/file:

```text
src/modules/map/map.ranking.ts
```

Suggested exports:

```ts
getMapDensityLimit(zoom)
getCandidateLimit(effectiveLimit)
scoreMapPlace(row, context)
rankMapPlaces(rows, context, requestedLimit)
```

- Keep API response shape stable for now:

```json
{
  "places": []
}
```

Do not expose score in public API yet.

## Database / RPC Changes

No schema migration required.

Optional improvement:

- keep `places_in_bbox(..., result_limit)` as-is;
- backend passes `candidateLimit`;
- RPC continues returning raw candidates.

This keeps TASKS 13 fully backend-code only.

## Frontend Impact

Frontend should send `zoom` when it can.

Swift map frameworks usually expose zoom indirectly through region/span or camera
altitude. For MVP, frontend can compute or approximate zoom and pass it. If the
frontend cannot produce a reliable zoom, it may omit the param and the backend
will derive density from the bbox span.

Backend owns final density:

- frontend can request `limit=200`;
- backend may still return `60` at zoom 13;
- this is expected.

## Test Plan

Unit tests:

- `getMapDensityLimit` returns expected limits for zoom buckets;
- `getCandidateLimit` caps at `400`;
- `rankMapPlaces`:
  - returns at most effective limit;
  - prefers higher rating/review rows;
  - is stable for identical inputs;
  - handles `null` rating/reviews.

Route/schema tests:

- missing `zoom` returns `200` and falls back to bbox-derived density;
- out-of-range zoom returns `400`;
- valid zoom returns `200`;
- service receives parsed `zoom` when present;
- zoom 10 returns fewer places than zoom 15 (density buckets apply).

Manual checks:

```bash
curl "http://localhost:3000/v1/map/places?city=Bucharest&swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=10&limit=200"

curl "http://localhost:3000/v1/map/places?city=Bucharest&swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=15&limit=200"
```

Expected:

- zoom 10 returns fewer places than zoom 15;
- both are stable across repeated calls;
- no API response includes internal score.

Run:

```bash
pnpm build
pnpm test
pnpm lint
```

## Assumptions

- We keep one endpoint for pins.
- We do not add user personalization yet.
- We do not add clustering yet.
- Current data volume is small enough for bbox overfetch + TypeScript ranking.
- Ranking quality can be simple today because the main goal is UX density and
  future personalization hook.

## Future Follow-Ups

- Add `userId`/auth context and taste score.
- Plug in Python recommendation service for `userTasteScore`.
- Add category filters into scoring/ranking.
- Add cluster response type for low zoom if density limiting is not enough.
- Add viewport cell/grid diversification so top 20 are not all on one street.
