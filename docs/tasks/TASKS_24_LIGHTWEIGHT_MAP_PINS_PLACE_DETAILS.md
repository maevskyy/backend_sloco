# TASKS 24: Lightweight Map Pins + Place Details API

## Summary

Split the place API into two read models:

- `GET /v1/map/places` returns only lightweight map pins for pan/zoom.
- `GET /v1/places/:placeId` returns the detailed place payload when the user
  taps a pin/card.

The current map response is too heavy after the enriched `places` V2 import. A
map pan does not need addresses, phones, websites, AI paragraphs, axes,
opening-hours blobs, provider details, or photo metadata. The frontend needs a
small marker feed first, then a detail fetch only for the selected place.

This task is intentionally **read-only** for places. Public/admin create,
update, and delete endpoints are not part of this task because place data is
still managed by imports and curated datasets.

## Key Changes

### Map Pins

Change `GET /v1/map/places` response shape to a slim `MapPlacePin`.

Pin fields:

```text
id
name
latitude
longitude
category
primaryType
rating
priceLevel
mapVisibilityScore
displayKind
displayPriority
primaryPhoto
isSaved
```

Do not return these from the map endpoint:

```text
source/sourceId
country/city
formattedAddress/shortFormattedAddress
businessStatus
googleMapsUri
phone/internationalPhone/websiteUrl
google/apify rating internals
price min/max
AI summaries and AI tags
taste axes
openingHours/serves/features
googleDetails/apifyDetails/aiDetails/photoDetails
rawCuisineStyle
savedCollectionIds
raw/attributes/provider blobs
```

`primaryPhoto` remains nullable and must be small metadata only. Do not proxy
photo bytes through the backend.

### Place Details

Add a new public endpoint:

```http
GET /v1/places/:placeId
Authorization: Bearer <supabase_access_token> optional
```

Auth behavior should match the map endpoint:

- no token: return public place detail with `isSaved=false`;
- valid token: return place detail enriched with user saved state;
- invalid token: `401 Unauthorized`.

Return `404` when the place does not exist.

`PlaceDetails` should include the rich fields removed from map pins:

```text
id, source, sourceId
name, country, city, category, primaryType, types
coordinates
address/contact/web fields
rating/price/review fields
map visibility signals
AI summaries/tags/confidence
taste axes
photo counts + primaryPhoto
openingHours/serves/features
googleDetails/apifyDetails/aiDetails/photoDetails if needed for the UI
isSaved
savedCollectionIds
```

Do not expose raw provider rows by default. If debug access is needed later,
make it a protected/admin-only endpoint or explicit debug query.

### Backend Structure

- Keep the `map` module focused on viewport pins.
- Add a new `places` module for place detail read API:
  - `common/places.schemas.ts`
  - `common/places.openapi.ts`
  - `common/places.types.ts`
  - `common/places.mappers.ts`
  - `controllers/places.controller.ts`
  - `services/places.service.ts`
  - `stores/places.store.ts`
  - tests next to the module.
- Register `GET /v1/places/:placeId` in `src/config/routes.ts` and Swagger.
- Reuse optional auth + saved-place enrichment patterns from `map` and
  `saved-places`.

### Database Access

Add migration `010_map_pins_place_details_rpc.sql`.

Create a new slim RPC for map pins:

```sql
public.map_places_in_bbox(
  sw_lat double precision,
  sw_lng double precision,
  ne_lat double precision,
  ne_lng double precision,
  result_limit integer
)
```

The map RPC must return only the DB fields needed for `MapPlacePin` ranking and
rendering. It should still use the existing PostGIS bbox index and current
visibility ordering.

Keep the existing `places_in_bbox` function during this task unless there is a
clear reason to remove it. The immediate goal is to move the runtime map API to
the slim function without breaking manual/debug SQL flows.

For details, either:

- use a typed Supabase `.from("places").select(<explicit columns>)` by `id`; or
- add `public.place_details_by_id(place_id bigint)` if SQL-level shaping is
  cleaner.

In both cases, use explicit column lists. Do not `select *`.

## API Contract

### `GET /v1/map/places`

Response:

```json
{
  "places": [
    {
      "id": 123,
      "name": "Vibe Anime Cafe",
      "latitude": 44.4451535,
      "longitude": 26.1002004,
      "category": "cafe",
      "primaryType": "cafe",
      "rating": 4.7,
      "priceLevel": 2,
      "mapVisibilityScore": 91,
      "displayKind": "featured",
      "displayPriority": 1,
      "primaryPhoto": {
        "path": "google/...",
        "url": "https://...",
        "width": 1200,
        "height": 900,
        "source": "review"
      },
      "isSaved": false
    }
  ]
}
```

### `GET /v1/places/:placeId`

Response:

```json
{
  "place": {
    "id": 123,
    "source": "google",
    "sourceId": "ChIJ...",
    "name": "Vibe Anime Cafe",
    "country": "Romania",
    "city": "Bucharest",
    "category": "cafe",
    "primaryType": "cafe",
    "types": ["cafe", "point_of_interest"],
    "latitude": 44.4451535,
    "longitude": 26.1002004,
    "formattedAddress": "...",
    "shortFormattedAddress": "...",
    "businessStatus": "OPERATIONAL",
    "googleMapsUri": "https://...",
    "phone": null,
    "internationalPhone": null,
    "websiteUrl": "https://...",
    "rating": 4.7,
    "priceLevel": 2,
    "numberOfReviews": 120,
    "aiCardSummary": "...",
    "aiVibe": "...",
    "aiWhatToExpect": "...",
    "aiTags": ["quiet", "specialty coffee"],
    "primaryPhoto": null,
    "totalPhotoCount": 12,
    "openingHours": {},
    "serves": {},
    "features": {},
    "isSaved": false,
    "savedCollectionIds": []
  }
}
```

The exact Swagger schema is the source of truth. The detail response may include
more typed UI fields, but it must not include raw provider dumps by default.

## Test Plan

- `GET /v1/map/places` returns slim pins and no detail-only fields.
- Map response does not include:
  - `googleDetails`;
  - `apifyDetails`;
  - `aiDetails`;
  - `photoDetails`;
  - `openingHours`;
  - `savedCollectionIds`;
  - contact/address fields.
- Map endpoint still applies density/ranking and saved `isSaved` state.
- `GET /v1/places/:placeId` returns full detail for an existing place.
- `GET /v1/places/:placeId` returns `404` for a missing place.
- Invalid `placeId` returns `400`.
- Optional auth behavior:
  - no token works;
  - valid token enriches saved state;
  - invalid token returns `401`.
- OpenAPI includes:
  - slim `MapPlacePin`;
  - `PlaceDetails`;
  - `/v1/places/{placeId}`.
- Run:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual checks:

```bash
curl "http://localhost:3000/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=100"
curl "http://localhost:3000/v1/places/123"
```

After deploy:

```bash
curl "https://sloco.pp.ua/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=100"
curl "https://sloco.pp.ua/v1/places/123"
```

## Acceptance Criteria

- Map response payload is materially smaller than the current V2 `MapPlace`
  response.
- Map panning no longer transfers full place metadata.
- Frontend can render the map from `GET /v1/map/places` alone.
- Frontend can open a place sheet/card by calling `GET /v1/places/:placeId`.
- Swagger is enough for frontend agents to regenerate/update their models.

## Assumptions

- TASKS 23 latency metrics will measure the before/after effect.
- Place data is still import-managed; no public/admin place mutation API in this
  task.
- Full photo gallery is not part of this task. Details can expose primary photo
  and photo counts; gallery endpoint can come later.
- Backward compatibility with the old fat map response is not required. Swift
  should follow the updated Swagger contract.
