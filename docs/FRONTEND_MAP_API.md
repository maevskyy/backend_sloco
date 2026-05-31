# Frontend Map API

This document describes how the iOS app should request map places from the backend.

## Base URL

Production:

```text
http://65.108.142.55
```

Local development:

```text
http://127.0.0.1:3000
```

Current production is HTTP-only. HTTPS will be added later when we attach a domain.

## Health Checks

Backend health:

```http
GET /v1/health
```

Example:

```bash
curl http://65.108.142.55/v1/health
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
curl http://65.108.142.55/v1/health/supabase
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
http://65.108.142.55/v1/swagger/openapi.json
```

Human Swagger UI:

```text
http://65.108.142.55/v1/swagger/docs
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

If auth is omitted, every place returns `isSaved: false` and
`savedCollectionIds: []`.
If auth is valid, `isSaved` and `savedCollectionIds` reflect the current user's
saved state.
If auth is invalid, the backend returns `401`.

Example:

```bash
curl "http://65.108.142.55/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
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
| `zoom` | number | no | Map zoom level (`1`-`22`). Controls how many places the backend returns. If omitted, density is derived from the bbox span. |
| `limit` | number | no | Optional cap, max `250`. Backend still clamps it against zoom-based density. |

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
map `zoom` and the backend returns ranked places with display metadata.

The response has two marker tiers:

- `featured`: normal/icon marker;
- `dot`: small lightweight point.

| Zoom | Level | Featured places | Total places |
| --- | --- | ---: | ---: |
| `<= 10` | whole city | 8 | 80 |
| `11-12` | large area | 12 | 120 |
| `13-14` | district / blocks | 20 | 180 |
| `15-16` | streets / blocks | 30 | 220 |
| `>= 17` | close view | 40 | 250 |

`limit` can only lower total places, never raise it. For example, requesting
`limit=20` at zoom `13` returns at most `20` total places. If `zoom` is omitted,
the backend derives display limits from the bbox span.

## Response

Successful response:

```json
{
  "places": [
    {
      "id": 1,
      "source": "tripadvisor",
      "sourceId": "d5529357",
      "name": "Pane e Vino",
      "country": "Germany",
      "city": "Berlin",
      "latitude": 52.552578,
      "longitude": 13.352883,
      "rating": 4,
      "priceLevel": 2,
      "numberOfReviews": 17,
      "rawCuisineStyle": null,
      "isSaved": false,
      "savedCollectionIds": [],
      "displayKind": "featured",
      "displayPriority": 1
    }
  ]
}
```

Fields:

| Field | Type | Description |
| --- | --- | --- |
| `id` | number | Backend row id. |
| `source` | string | Data source, e.g. `tripadvisor` or `osm`. |
| `sourceId` | string | Source-specific id, e.g. TripAdvisor id or OSM id. |
| `name` | string | Place name. |
| `country` | string | Country name or code from the normalized place record. |
| `city` | string | City name. |
| `latitude` | number | Pin latitude. |
| `longitude` | number | Pin longitude. |
| `rating` | number or null | TripAdvisor rating. |
| `priceLevel` | number or null | Normalized price level from `1` to `4`. |
| `numberOfReviews` | number or null | Number of source reviews. |
| `rawCuisineStyle` | string or null | Raw cuisine/tags string from source data. |
| `isSaved` | boolean | Whether the authenticated user saved this place. Public map requests return `false`. |
| `savedCollectionIds` | string[] | Collection ids containing the place for the authenticated user. Public map requests return `[]`. |
| `displayKind` | `"featured"` or `"dot"` | Rendering hint. Use `featured` for normal markers and `dot` for small lightweight points. |
| `displayPriority` | number | 1-based ranking position in the current bbox. Lower number means higher priority. |

The response does not include heavy fields like reviews or embedding text.
Missing numeric signals are returned as `null`.

## Swift Model Example

```swift
struct MapPlacesResponse: Decodable {
    let places: [MapPlace]
}

struct MapPlace: Decodable, Identifiable {
    let id: Int
    let source: String
    let sourceId: String
    let name: String
    let country: String
    let city: String
    let latitude: Double
    let longitude: Double
    let rating: Double?
    let priceLevel: Int?
    let numberOfReviews: Int?
    let rawCuisineStyle: String?
    let isSaved: Bool
    let savedCollectionIds: [String]
    let displayKind: DisplayKind
    let displayPriority: Int
}

enum DisplayKind: String, Decodable {
    case featured
    case dot
}
```

## Swift Request Example

```swift
func fetchMapPlaces() async throws -> [MapPlace] {
    var components = URLComponents(string: "http://65.108.142.55/v1/map/places")!
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
7. When the map region changes, iOS should debounce requests.

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
http://65.108.142.55/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
```
