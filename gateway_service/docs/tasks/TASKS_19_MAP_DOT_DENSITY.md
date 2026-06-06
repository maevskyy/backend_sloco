# TASKS 19: Map Dot Density And Display Kind

## Summary

Increase map density without returning every place in the viewport.

The frontend wants a Corner-like map:

- a small number of normal/icon markers;
- many lightweight dot markers;
- enough density that the city feels alive;
- no unbounded "return everything in bbox" behavior.

Current backend has a single density limit:

```ts
zoom < 11   -> 8
zoom 11-12  -> 15
zoom 13-14  -> 25
zoom >= 15  -> 40
```

That is too sparse for dot rendering. The fix is **not** simply making the limit
huge. The fix is splitting map places into two display tiers:

```ts
displayKind: "featured" | "dot"
displayPriority: number
```

No database migration is required. These fields are computed at runtime from the
map ranking result.

## Product Behavior

Zoom meaning:

```text
zoom 5-8    country / region
zoom 9-10   whole city
zoom 11-12  large city area
zoom 13-14  district / several blocks
zoom 15-16  streets / blocks
zoom 17+    close view: buildings / plaza / food court
```

Lower zoom means the user sees a larger area. Higher zoom means the user sees a
smaller area.

We should not show only 20 places for an entire city if dot markers exist.
Instead:

- `featuredLimit` controls normal/icon markers;
- `totalLimit` controls all returned places, including dots.

The first `featuredLimit` ranked places are shown as `featured`.
The rest up to `totalLimit` are shown as `dot`.

## Target Display Limits

Initial MVP limits:

```ts
zoom <= 10:
  featuredLimit: 8
  totalLimit: 80

zoom 11-12:
  featuredLimit: 12
  totalLimit: 120

zoom 13-14:
  featuredLimit: 20
  totalLimit: 180

zoom 15-16:
  featuredLimit: 30
  totalLimit: 220

zoom >= 17:
  featuredLimit: 40
  totalLimit: 250
```

Reasoning:

- low zoom still gets enough dots to make the city feel populated;
- normal markers stay readable;
- high zoom can show more places because bbox is smaller;
- 250 is still capped, so dense food courts / centers do not return unlimited
  points.

## Ranking Strategy

Current ranking is not random.

Current backend score:

```ts
ratingScore = rating * 10
reviewsScore = min(log10(reviews + 1) * 5, 20)
sourceScore = 5 for tripadvisor
jitter = stable 0..1 tie-breaker from source_id
```

`stableJitter` is deterministic, not random. It only breaks score ties so equal
places do not always fall back to DB id order.

Keep the backend score as the final ranking source.

## DB Candidate Strategy

Current backend overfetches candidates:

```ts
CANDIDATE_OVERFETCH = 4
MAX_CANDIDATES = 400
```

But the SQL RPC currently caps candidates at 200:

```sql
limit least(greatest(coalesce(result_limit, 100), 1), 200)
```

For dot density, raise the SQL cap and backend cap.

Target:

```ts
MAX_CANDIDATES = 1000
```

```sql
limit least(greatest(coalesce(result_limit, 100), 1), 1000)
```

The API still returns at most `totalLimit` places. Candidate overfetch only gives
the scorer more rows to choose from.

## SQL Pre-Sort Improvement

Current SQL pre-sort is mostly:

```sql
rating not null first,
rating desc,
id asc
```

This can over-prioritize a `5.0` place with 1 review above a `4.7` place with
2000 reviews before backend scoring ever sees enough candidates.

Improve SQL pre-sort to roughly align with backend quality:

```sql
case when p.rating is null then 1 else 0 end,
(
  coalesce(p.rating, 0) * 10
  + least(log(greatest(coalesce(p.reviews_count, 0), 0) + 1) * 5, 20)
) desc,
p.rating desc nulls last,
p.reviews_count desc nulls last,
p.id asc
```

This is still only candidate ordering. Backend ranking remains the final source
of truth.

## API Contract Changes

Add fields to map place response:

```ts
displayKind: "featured" | "dot"
displayPriority: number
```

Example:

```json
{
  "id": 123,
  "name": "Quiet Coffee",
  "displayKind": "featured",
  "displayPriority": 1
}
```

`displayPriority` is 1-based rank after backend scoring:

```text
1 = best ranked place in current bbox
```

Frontend behavior:

- render `featured` as normal marker/icon/card marker;
- render `dot` as small lightweight point;
- preserve `displayPriority` if it wants stable z-index or tap priority.

## Implementation Plan

### 1. Update Map Types And Schemas

Files:

```text
src/modules/map/common/map.schemas.ts
src/modules/map/common/map.types.ts
src/modules/map/common/map.openapi.ts
```

Add:

```ts
displayKind: z.enum(["featured", "dot"])
displayPriority: z.number().int().min(1)
```

Keep OpenAPI component ids stable:

- `MapPlace`
- `MapPlacesResponse`
- `MapPlacesQuery`

Only add fields; do not rename existing fields.

### 2. Replace Single Density Limit With Display Limits

File:

```text
src/modules/map/common/map.ranking.ts
```

Replace / supplement:

```ts
getMapDensityLimit()
getDensityLimit()
getEffectiveLimit()
```

with:

```ts
type MapDisplayLimits = {
  featuredLimit: number;
  totalLimit: number;
};

getMapDisplayLimits(zoom: number): MapDisplayLimits
getDisplayLimits(bbox, zoom?): MapDisplayLimits
getEffectiveDisplayLimits(userLimit, displayLimits): MapDisplayLimits
```

Important behavior:

- `userLimit` can narrow `totalLimit`, never widen it;
- `featuredLimit` must never exceed `totalLimit`;
- for debug calls with `limit=20`, return max 20 total places.

### 3. Mark Ranked Places With Display Metadata

File:

```text
src/modules/map/services/map.service.ts
```

After ranking:

```ts
ranked.map((place, index) => ({
  ...mapPlaceRowToPin(place),
  displayKind: index < featuredLimit ? "featured" : "dot",
  displayPriority: index + 1
}))
```

Keep saved-state enrichment behavior unchanged.

### 4. Increase Candidate Caps

File:

```text
src/modules/map/common/map.ranking.ts
```

Change:

```ts
MAX_CANDIDATES = 1000
```

Keep:

```ts
CANDIDATE_OVERFETCH = 4
```

This means for `totalLimit=250`, backend asks DB for up to 1000 candidates.

### 5. Update Supabase RPC Migration

Add a new migration, do not edit old applied migrations:

```text
supabase/migrations/007_update_places_in_bbox_candidate_order.sql
```

Migration should:

- `create or replace function public.places_in_bbox(...)`;
- keep the exact same function signature and return columns;
- raise SQL cap from 200 to 1000;
- improve candidate ordering with rating + reviews quality score.

No table changes.

### 6. Update Tests

Files:

```text
src/modules/map/tests/map.ranking.test.ts
src/modules/map/tests/map.service.test.ts
src/modules/map/tests/map.routes.test.ts
src/modules/map/tests/map.mappers.test.ts
```

Test cases:

- display limits by zoom:
  - zoom 10 -> `8 / 80`;
  - zoom 12 -> `12 / 120`;
  - zoom 14 -> `20 / 180`;
  - zoom 16 -> `30 / 220`;
  - zoom 17 -> `40 / 250`.
- `limit` narrows total places but does not exceed backend cap.
- first `featuredLimit` places are `featured`;
- remaining places are `dot`;
- `displayPriority` is 1-based and stable;
- route response contains `displayKind` and `displayPriority`.

### 7. Update Frontend Docs

File:

```text
docs/FRONTEND_MAP_API.md
```

Document:

- `displayKind`;
- `displayPriority`;
- suggested rendering:
  - `featured` = normal marker;
  - `dot` = tiny marker;
- backend still caps total places.

## Future Improvement: Spatial Thinning

This task does not implement spatial thinning.

If dots look like a dense cluster / "ветрянка", add a later algorithm:

```text
rank places by score
bucket them into viewport/grid cells
keep best N per cell
fill remaining slots by score
```

That prevents 100 points from occupying the same food court corner while another
part of the viewport looks empty.

Possible future fields:

```ts
clusterKey?: string
screenBucket?: string
```

Not needed for this task.

## Test Plan

```bash
pnpm build
pnpm test
pnpm lint
```

Manual smoke:

```bash
curl "http://127.0.0.1:3000/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

Check:

- response has more places than before;
- first items are `featured`;
- later items are `dot`;
- no response exceeds the `totalLimit` for zoom;
- OpenAPI still exposes the same path and component ids.

## Assumptions

- Frontend can render `featured` and `dot` differently.
- No DB migration is needed for `displayKind` / `displayPriority`.
- SQL function migration will be applied manually in Supabase before production
  deploy if needed.
- This task keeps personalization weight at zero until the taste service exists.
