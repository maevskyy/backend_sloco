# Frontend Map API

This document describes how the iOS app should request map places from the backend.

## Base URL

Production:

```text
https://sloco.pp.ua
```

Local development:

```text
http://127.0.0.1:3000
```

Production uses HTTPS through Nginx + Let's Encrypt.

## Health Checks

Backend health:

```http
GET /v1/health
```

Example:

```bash
curl https://sloco.pp.ua/v1/health
```

Expected response:

```json
{
  "status": "ok"
}
```

Supabase/database health:

```http
GET /v1/health/supabase
```

Example:

```bash
curl https://sloco.pp.ua/v1/health/supabase
```

Expected response:

```json
{
  "status": "ok"
}
```

## OpenAPI Contract

Frontend agents should read this contract before generating models or API
clients:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

Human Swagger UI:

```text
https://sloco.pp.ua/v1/swagger/docs
```

Use OpenAPI as the source of truth if this Markdown doc ever drifts.

## Map Places Endpoint

Use this endpoint when the map opens or when the visible map region changes.

```http
GET /v1/map/places
```

The endpoint is public, but can also accept auth:

```http
Authorization: Bearer <supabase_access_token>
```

If auth is omitted, every place returns `isSaved: false`.
If auth is valid, `isSaved` reflects the current user's saved state.
If auth is invalid, the backend returns `401`.

Example:

```bash
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

## Query Parameters

Required:

```text
swLat
swLng
neLat
neLng
```

Optional:

```text
zoom
limit
```

Parameters:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `swLat` | number | yes | South-west map corner latitude. |
| `swLng` | number | yes | South-west map corner longitude. |
| `neLat` | number | yes | North-east map corner latitude. |
| `neLng` | number | yes | North-east map corner longitude. |
| `zoom` | number | no | Map zoom level (`1`-`22`). Controls the backend visibility-score threshold. If omitted, zoom is derived from the bbox span. |
| `limit` | number | no | Optional safety cap, max `400`. Normal map usage should omit it. It is not the density control. |

The frontend gets these values from the current visible map rectangle.

For Berlin test data, use this full viewport:

```text
swLat=52.4800
swLng=13.3300
neLat=52.5600
neLng=13.4700
```

## Density, Zoom, And Marker Display

The backend, not the frontend, decides how many places to show. Send the current
map `zoom` and the backend returns places whose own `mapVisibilityScore` passes
the zoom threshold. This makes pin membership stable while panning at the same
zoom: a place that remains inside the bbox does not disappear merely because
other places entered the new bbox.

The response has two marker tiers:

- `featured`: normal/icon marker;
- `dot`: small lightweight point.

| Zoom | Level | Min score | Featured min score |
| --- | --- | ---: | ---: |
| `<= 10` | whole city | 92 | 98 |
| `11-12` | large area | 86 | 95 |
| `13-14` | district / blocks | 76 | 92 |
| `15-16` | streets / blocks | 66 | 88 |
| `>= 17` | close view | 56 | 84 |

`limit` is only a safety cap. Sending a small `limit` intentionally opts back
into count clipping and can reintroduce pin churn for that client. Normal map
requests should omit `limit` and let the backend score threshold decide density.
If `zoom` is omitted, the backend derives an effective zoom from the bbox span.

## Response

Successful response:

```json
{
  "places": [
    {
      "id": 1,
      "name": "Pane e Vino",
      "category": "restaurant",
      "primaryType": "restaurant",
      "latitude": 52.552578,
      "longitude": 13.352883,
      "rating": 4,
      "priceLevel": 2,
      "mapVisibilityScore": 91,
      "primaryPhoto": null,
      "isSaved": false,
      "displayKind": "featured",
      "displayPriority": 1
    }
  ],
  "meta": {
    "returnedCount": 1,
    "limit": 400,
    "requestedLimit": null,
    "candidateLimit": 400,
    "capped": false,
    "effectiveZoom": 13,
    "minScore": 76,
    "featuredMinScore": 92,
    "safetyCap": 400,
    "capHit": false,
    "queryBounds": {
      "swLat": 52.48,
      "swLng": 13.33,
      "neLat": 52.56,
      "neLng": 13.47
    }
  }
}
```

Fields:

| Field | Type | Description |
| --- | --- | --- |
| `id` | number | Backend row id. |
| `name` | string | Place name. |
| `category` | string | Normalized category, e.g. `cafe`, `restaurant`, `bar`. |
| `primaryType` | string or null | Provider/domain primary type. |
| `latitude` | number | Pin latitude. |
| `longitude` | number | Pin longitude. |
| `rating` | number or null | Display rating. |
| `priceLevel` | number or null | Normalized price level from `0` to `4`. |
| `mapVisibilityScore` | number | Backend ranking/visibility signal. |
| `primaryPhoto` | object or null | Small primary photo metadata for marker/card display. |
| `isSaved` | boolean | Whether the authenticated user saved this place. Public map requests return `false`. |
| `displayKind` | `"featured"` or `"dot"` | Rendering hint. Use `featured` for normal markers and `dot` for small lightweight points. |
| `displayPriority` | number | 1-based ranking position in the current bbox. Lower number means higher priority. |

`meta.capHit=true` means the backend hit the safety cap and the response may
again be clipped by count. If this happens often for normal map views, backend
thresholds need recalibration or clustering/tiles.

The map response is intentionally a lightweight pin feed. It does not include
address, phone, website, AI summaries, opening hours, provider details, full
photo metadata, saved collection ids, or raw provider blobs. When a user taps a
place, call `GET /v1/places/:placeId` for the full detail payload.

Missing numeric signals are returned as `null`.

## Swift Model Example

```swift
struct MapPlacesResponse: Decodable {
    let places: [MapPlace]
    let meta: MapPlacesMeta
}

struct MapPlacesMeta: Decodable {
    let returnedCount: Int
    let limit: Int
    let requestedLimit: Int?
    let candidateLimit: Int
    let capped: Bool
    let effectiveZoom: Int
    let minScore: Double
    let featuredMinScore: Double
    let safetyCap: Int
    let capHit: Bool
    let queryBounds: QueryBounds
}

struct QueryBounds: Decodable {
    let swLat: Double
    let swLng: Double
    let neLat: Double
    let neLng: Double
}

struct MapPlace: Decodable, Identifiable {
    let id: Int
    let name: String
    let category: String
    let primaryType: String?
    let latitude: Double
    let longitude: Double
    let rating: Double?
    let priceLevel: Int?
    let mapVisibilityScore: Double
    let primaryPhoto: PrimaryPhoto?
    let isSaved: Bool
    let displayKind: DisplayKind
    let displayPriority: Int
}

struct PrimaryPhoto: Decodable {
    let path: String
    let url: String?
    let width: Int?
    let height: Int?
    let source: String?
}

enum DisplayKind: String, Decodable {
    case featured
    case dot
}
```

## Swift Request Example

```swift
func fetchMapPlaces() async throws -> [MapPlace] {
    var components = URLComponents(string: "https://sloco.pp.ua/v1/map/places")!
    components.queryItems = [
        URLQueryItem(name: "swLat", value: "52.4800"),
        URLQueryItem(name: "swLng", value: "13.3300"),
        URLQueryItem(name: "neLat", value: "52.5600"),
        URLQueryItem(name: "neLng", value: "13.4700"),
        URLQueryItem(name: "zoom", value: "13")
    ]

    let (data, response) = try await URLSession.shared.data(from: components.url!)

    guard let httpResponse = response as? HTTPURLResponse,
          (200..<300).contains(httpResponse.statusCode) else {
        throw URLError(.badServerResponse)
    }

    let decoded = try JSONDecoder().decode(MapPlacesResponse.self, from: data)
    return decoded.places
}
```

## Place Details Endpoint

Use this endpoint after the user taps a map pin or opens a place card.

```http
GET /v1/places/:placeId
```

Auth is optional and works like the map endpoint:

- no token: returns public details with `isSaved: false`;
- valid token: enriches `isSaved` and `savedCollectionIds`;
- invalid token: returns `401`.

Example:

```bash
curl "https://sloco.pp.ua/v1/places/123"
```

The detail response is intentionally richer than the map response. It can
include address/contact fields, rating internals, AI summaries, taste axes,
opening hours, feature JSON, photo counts, and saved collection ids. The exact
shape is defined by Swagger:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
```

## Map Usage

Frontend flow:

1. User opens the map.
2. iOS reads the visible map region.
3. iOS converts it into:

   ```text
   swLat
   swLng
   neLat
   neLng
   ```

   and reads the current map `zoom` level.

4. iOS calls `GET /v1/map/places` with the bbox and `zoom`.
5. iOS renders one marker per item using:

   ```text
   latitude
   longitude
   ```

   Use `displayKind == .featured` for normal markers and `displayKind == .dot`
   for small lightweight points.

6. Use `displayPriority` for z-index / tap priority if markers overlap.
7. When the user taps a marker/card, call `GET /v1/places/:placeId`.
8. When the map region changes, iOS should debounce requests.

Recommended debounce:

```text
300-700 ms after region change ends
```

Do not call the backend on every pixel of map movement.

## Error Responses

Invalid query params:

```http
400 Bad Request
```

Example response:

```json
{
  "status": "error",
  "message": "Invalid map places query",
  "issues": []
}
```

Backend/database error:

```http
500 Internal Server Error
```

Example response:

```json
{
  "status": "error"
}
```

## Saved Places API

Use these endpoints after Supabase Auth login.

Auth header:

```http
Authorization: Bearer <session.access_token>
```

Get Saved dashboard:

```http
GET /v1/me/saved
```

Response:

```json
{
  "summary": {
    "savedPlaceCount": 6,
    "collectionCount": 3,
    "recommendationsUseSavedPlaces": true
  },
  "collections": [],
  "recentlySaved": []
}
```

Get collection detail:

```http
GET /v1/me/saved/collections/:collectionId
```

Response:

```json
{
  "collection": {},
  "places": [],
  "availableCollections": []
}
```

Save place:

```http
POST /v1/me/saved/places
Content-Type: application/json
```

Body:

```json
{
  "placeId": 123,
  "collectionIds": ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"]
}
```

If `collectionIds` is omitted, backend adds the place to the user's default
`Want to go` collection.

Response:

```json
{
  "placeId": 123,
  "isSaved": true,
  "collectionIds": ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"],
  "savedAt": "2026-05-31T10:00:00.000Z"
}
```

Unsave place:

```http
DELETE /v1/me/saved/places/:placeId
```

Response:

```json
{
  "placeId": 123,
  "isSaved": false,
  "collectionIds": []
}
```

Collection mutations:

```http
POST /v1/me/saved/collections
PATCH /v1/me/saved/collections/:collectionId
DELETE /v1/me/saved/collections/:collectionId
POST /v1/me/saved/collections/:collectionId/places
DELETE /v1/me/saved/collections/:collectionId/places/:placeId
PATCH /v1/me/saved/collections/:collectionId/places/order
```

Use Swagger as source of truth for exact body and response schemas.

Frontend can optimistically flip the saved heart and revert if the backend
returns an error.

## Current Limitations

- Only Berlin test data is available.
- Coordinates are randomly distributed inside central Berlin.
- This is not accurate geocoding yet.
- No personalization score yet.
- No `whyRecommended` yet.
- No category filtering yet.

## Quick Test URL

Open this in a browser or use curl:

```text
https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
```
