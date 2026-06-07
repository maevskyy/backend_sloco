# Frontend Handoff: Production Map on Mapbox Vector Tiles

For the iOS dev. We're moving the map from "fetch JSON pins per bbox + place
annotations by hand" to **Mapbox vector tiles** — the production, non-laggy model.

You already have the expensive part: **Mapbox is a GPU vector renderer.** This
change is about *feeding it correctly*, not switching SDKs. Most of the current
lag comes from hand-placing/redrawing annotations instead of letting Mapbox
render a tile source.

Backend spec: `docs/tasks/TASKS_32_MAP_VECTOR_TILES.md`. Source of truth for the
contract: the OpenAPI doc.

## What changes (mental model)

- **Stop fetching `/v1/map/places` and creating annotations manually.**
- Add a **Mapbox vector source** pointing at the tile endpoint. Mapbox requests
  tiles itself as the user pans/zooms, caches them, and renders on the GPU.
- Style pins with **style layers + data-driven expressions** (size/icon/color by
  tile attributes). Mapbox handles label collision, zoom transitions, panning.
- `isSaved` is **not in the tile** (tiles are shared/cached). Apply it with
  **`setFeatureState`** from a separate per-user call.
- Tapping a pin → fetch full detail from `/v1/places/:id` (unchanged).

Result: smooth pan/zoom, no churn, no manual annotation management.

## 1. Add the vector tile source

```
Tile URL template:
https://sloco.pp.ua/v1/map/tiles/{z}/{x}/{y}.mvt?v={DATA_VERSION}
```

- `{z}/{x}/{y}` — standard slippy/XYZ tile coords. Mapbox fills these in.
- `?v={DATA_VERSION}` — data version for cache-busting. The backend returns the
  current version (in tile `ETag`/headers and via a small config endpoint). When
  it changes, update the source URL (see §6).
- The MVT layer name inside each tile is **`places`** → that's your `sourceLayer`.
- Each feature's **`id` is the backend place id** (set via `ST_AsMVT` feature id)
  → `setFeatureState` works directly, no `promoteId` needed.
- Fetch current config first:
  ```
  GET /v1/map/config
  → { "tileVersion": 1, "tileUrlTemplate": "/v1/map/tiles/{z}/{x}/{y}.mvt?v=1", "sourceLayer": "places" }
  ```

iOS (Mapbox Maps SDK v11):

```swift
var source = VectorSource(id: "places-src")
source.tiles = ["https://sloco.pp.ua/v1/map/tiles/{z}/{x}/{y}.mvt?v=\(dataVersion)"]
source.minzoom = 8
source.maxzoom = 16            // backend generalizes; client can overzoom past this
try mapView.mapboxMap.addSource(source)
```

## 2. Add style layers (let Mapbox render, don't hand-place)

Available feature attributes in each tile (light, for styling only):
`id, name, category, primaryType, priceLevel, mapVisibilityScore, primaryPhotoPath`.

A circle layer for the dots + a symbol layer for icons/labels:

```swift
// Dots / markers
var circle = CircleLayer(id: "places-circle", source: "places-src")
circle.sourceLayer = "places"
// size grows with visibility score
circle.circleRadius = .expression(
  Exp(.interpolate) { Exp(.linear); Exp(.get) { "mapVisibilityScore" }
    50; 3
    100; 7
  })
circle.circleColor = .constant(StyleColor(.systemOrange))
try mapView.mapboxMap.addLayer(circle)

// Labels (Mapbox auto-hides overlapping ones)
var label = SymbolLayer(id: "places-label", source: "places-src")
label.sourceLayer = "places"
label.textField = .expression(Exp(.get) { "name" })
label.textAllowOverlap = .constant(false)          // collision handling
label.symbolSortKey = .expression(                 // important pins win
  Exp(.subtract) { 100; Exp(.get) { "mapVisibilityScore" } })
try mapView.mapboxMap.addLayer(label)
```

You can also drive `iconImage` by `category` with a `match` expression.

## 3. isSaved via feature-state (per-user, no tile reload)

The tile is shared and cached, so saved state is applied client-side:

1. Fetch the user's saved ids once (and after save/unsave):
   ```
   GET /v1/me/saved/ids        (Authorization: Bearer <token>)
   → { "placeIds": [123, 456, ...] }
   ```
2. Set feature-state for each:
   ```swift
   for id in placeIds {
     mapView.mapboxMap.setFeatureState(
       sourceId: "places-src", sourceLayerId: "places",
       featureId: String(id), state: ["isSaved": true])
   }
   ```
3. Style by feature-state (e.g. saved = filled heart / accent color):
   ```swift
   circle.circleColor = .expression(
     Exp(.switchCase) {
       Exp(.boolean) { Exp(.featureState) { "isSaved" }; false }
       StyleColor(.systemRed)        // saved
       StyleColor(.systemOrange)     // default
     })
   ```
On save/unsave, just flip the one feature's state — no refetch, no tile reload.

## 4. Tap → place detail (unchanged)

```swift
mapView.mapboxMap.queryRenderedFeatures(
  with: tapPoint, options: RenderedQueryOptions(layerIds: ["places-circle"], filter: nil)
) { result in
  if case let .success(features) = result,
     let id = features.first?.queriedFeature.feature.identifier {
    // id == backend place id → fetch full details
    // GET /v1/places/\(id)
  }
}
```

## 5. What to remove

- Manual `PointAnnotation` / `MGLAnnotation` creation from `/v1/map/places` JSON.
- Any client-side clustering/dedup/sticky-id hacks for pin churn — the tile model
  is stable by construction.
- Per-pan full refetch of pins — Mapbox fetches only the tiles it needs.

## 6. Cache / versioning

- Mapbox caches tiles itself; the `?v={DATA_VERSION}` in the URL is the bust key.
- When the backend bumps the data version (new places import), update the source:
  ```swift
  try mapView.mapboxMap.updateGeoJSONSourceFeatures(...)   // N/A for vector
  // For a vector source: recreate it with the new ?v=, or set the tiles URL again:
  try mapView.mapboxMap.setSourceProperty(
    for: "places-src", property: "tiles",
    value: ["https://sloco.pp.ua/v1/map/tiles/{z}/{x}/{y}.mvt?v=\(newVersion)"])
  ```
- Get the current version from `GET /v1/map/config` (`tileVersion`). Tile responses
  also include an ETag for HTTP caching.

## 7. Clustering note

We rely on **backend generalization** (fewer pins at low zoom) + **Mapbox label
collision**, which matches how Google shows fewer labels when zoomed out. There
are **no numeric cluster bubbles** ("23 places") in v1 — if product wants them
later, backend adds a separate aggregate layer. Don't build client-side
clustering on top of the vector source.

## TL;DR checklist

- [ ] Add `VectorSource` → tiles `…/v1/map/tiles/{z}/{x}/{y}.mvt?v={version}`,
      `sourceLayer = "places"`.
- [ ] Add circle + symbol layers, data-driven by `mapVisibilityScore` / `category`,
      `textAllowOverlap = false`, `symbolSortKey` by score.
- [ ] Remove manual annotations + churn hacks.
- [ ] `GET /v1/me/saved/ids` → `setFeatureState({isSaved})`, style by feature-state.
- [ ] Tap → `feature.id` → `GET /v1/places/:id`.
- [ ] Refresh source URL when `DATA_VERSION` changes.
```
