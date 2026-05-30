# Frontend Map API

This document describes how the iOS app should request map places from the backend.

## Base URL

Production:

```text
http://52.18.13.69
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
curl http://52.18.13.69/v1/health
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
curl http://52.18.13.69/v1/health/supabase
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
http://52.18.13.69/v1/swagger/openapi.json
```

Human Swagger UI:

```text
http://52.18.13.69/v1/swagger/docs
```

Use OpenAPI as the source of truth if this Markdown doc ever drifts.

## Map Places Endpoint

Use this endpoint when the map opens or when the visible map region changes.

```http
GET /v1/map/places
```

Example:

```bash
curl "http://52.18.13.69/v1/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100"
```

## Query Parameters

Required:

```text
city
swLat
swLng
neLat
neLng
```

Optional:

```text
limit
```

Parameters:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `city` | string | yes | City name. For now use `Berlin`. |
| `swLat` | number | yes | South-west map corner latitude. |
| `swLng` | number | yes | South-west map corner longitude. |
| `neLat` | number | yes | North-east map corner latitude. |
| `neLng` | number | yes | North-east map corner longitude. |
| `limit` | number | no | Max places to return. Default `100`, max `200`. |

The frontend gets these values from the current visible map rectangle.

For Berlin test data, use this full viewport:

```text
swLat=52.4800
swLng=13.3300
neLat=52.5600
neLng=13.4700
```

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
      "city": "Berlin",
      "latitude": 52.552578,
      "longitude": 13.352883,
      "rating": 4,
      "priceRange": "$$ - $$$",
      "numberOfReviews": 17,
      "rawCuisineStyle": null
    }
  ]
}
```

Fields:

| Field | Type | Description |
| --- | --- | --- |
| `id` | number | Backend row id. |
| `source` | string | Data source. Currently always `tripadvisor`. |
| `sourceId` | string | Source-specific id, TripAdvisor id for now. |
| `name` | string | Place name. |
| `city` | string | City name. |
| `latitude` | number | Pin latitude. |
| `longitude` | number | Pin longitude. |
| `rating` | number or null | TripAdvisor rating. |
| `priceRange` | string or null | Raw price range, e.g. `$`, `$$ - $$$`, `$$$$`. |
| `numberOfReviews` | number or null | Number of TripAdvisor reviews. |
| `rawCuisineStyle` | string or null | Raw cuisine/tags string from source data. |

The response does not include heavy fields like reviews or embedding text.

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
    let city: String
    let latitude: Double
    let longitude: Double
    let rating: Double?
    let priceRange: String?
    let numberOfReviews: Int?
    let rawCuisineStyle: String?
}
```

## Swift Request Example

```swift
func fetchMapPlaces() async throws -> [MapPlace] {
    var components = URLComponents(string: "http://52.18.13.69/v1/map/places")!
    components.queryItems = [
        URLQueryItem(name: "city", value: "Berlin"),
        URLQueryItem(name: "swLat", value: "52.4800"),
        URLQueryItem(name: "swLng", value: "13.3300"),
        URLQueryItem(name: "neLat", value: "52.5600"),
        URLQueryItem(name: "neLng", value: "13.4700"),
        URLQueryItem(name: "limit", value: "100")
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

4. iOS calls `GET /v1/map/places`.
5. iOS renders one pin per item using:

   ```text
   latitude
   longitude
   ```

6. When the map region changes, iOS should debounce requests.

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

## Current Limitations

- Only Berlin test data is available.
- Coordinates are randomly distributed inside central Berlin.
- This is not accurate geocoding yet.
- No auth yet.
- No saved state yet.
- No personalization score yet.
- No `whyRecommended` yet.
- No category filtering yet.

## Quick Test URL

Open this in a browser or use curl:

```text
http://52.18.13.69/v1/map/places?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&limit=100
```
