# Frontend Handoff: Stable Map Pins

Short note for the iOS/frontend dev about the map pin stability change
(`TASKS_28`). For the full endpoint reference see `docs/FRONTEND_MAP_API.md` and
the OpenAPI contract at `https://sloco.pp.ua/v1/swagger/openapi.json`.

## TL;DR

Map pins are now **stable when you pan at the same zoom**. A pin that stays
inside the visible rectangle will no longer randomly disappear and reappear when
you scroll the map.

This is mostly a **backend-side** stability change, but the marker tier contract
changed after frontend/backend agreement: the backend no longer returns
`displayKind` (`featured` / `dot`). The frontend owns marker style.

## What changed (conceptually)

Before: the backend returned the **top-N places inside the current rectangle**.
N was relative to whatever else was in view, so panning shifted the cutoff and a
pin near the edge of that cutoff could pop in and out.

Now: a place is shown if its **own** visibility score passes a per-zoom
threshold (`mapVisibilityScore >= minScore(zoom)`). Membership depends only on
the place and the zoom — not on its neighbors — so panning at the same zoom does
not drop a visible pin.

Consequence: the number of returned pins is now **variable** — denser areas
return more, sparser areas fewer (Google-Maps-like), instead of a fixed ~N.

## What you should do

1. **Keep sending `zoom`.** It selects the threshold. If you omit it, the backend
   derives an effective zoom from the bbox span (less precise).
2. **Do NOT send `limit` in normal map usage.** It is now only a safety cap.
   Sending a small `limit` re-enables count clipping and can bring the churn
   back for that request. Just omit it.
3. **Stop assuming a fixed pin count.** Don't hardcode "~180 pins"; size buffers
   and rendering off the actual returned array.
4. **You can remove client-side churn workarounds.** If you added sticky-id
   merging, cross-request dedup, or fade-in/out hacks to hide popping pins, they
   are no longer needed for same-zoom pan. (Keep your normal diff/animation on
   real changes.)

## Behavior to expect

- **Pan at the same zoom** → the set of pins for the area is stable; pins that
  stay in view stay rendered.
- **Zoom in/out** → the set legitimately changes (more/fewer pins). This is
  intentional, not churn — animate it if you want.
- **Marker style is frontend-owned.** Use `mapVisibilityScore`,
  `displayPriority`, current zoom, and local UI rules to decide which pins are
  large markers and which are small points.
- **Dense city centers** can return many pins (up to a safety cap). If the cap is
  hit, `meta.capHit` is `true` (see below).

## New `meta` fields (optional, for debug HUD)

The response `meta` object gained diagnostic fields. They are optional to decode
and safe to ignore in production:

| Field | Meaning |
| --- | --- |
| `effectiveZoom` | Zoom the backend actually applied (derived if you omitted `zoom`). |
| `minScore` | Visibility-score threshold used for membership at this zoom. |
| `safetyCap` | Max pins the backend will return for this request. |
| `capHit` | `true` if the safety cap was hit → response may be truncated in a very dense area. |
| `returnedCount` | Number of pins returned. |
| `requestedLimit` | The `limit` you sent, or `null`. |
| `queryBounds` | Echo of the bbox you requested. |

If `capHit` is frequently `true` for normal map views, tell backend — it means
thresholds need recalibration (or it is time for clustering/tiles). For a debug
HUD, surfacing `effectiveZoom`, `minScore`, `returnedCount`, and `capHit` is the
most useful.

## Contract / models

- Endpoint and query params are unchanged.
- `MapPlacePin` no longer contains `displayKind`.
- `MapPlacesMeta` no longer contains `featuredMinScore`.
- If your models are strict, update them; the current Swift example lives in
  `docs/FRONTEND_MAP_API.md`.
- Source of truth remains the OpenAPI contract.

## Quick check

```bash
# Same area, two overlapping rectangles at the same zoom → a pin present in one
# should be present in the other (no flip). Compare ids:
curl -s 'https://sloco.pp.ua/v1/map/places?swLat=44.40&swLng=26.06&neLat=44.46&neLng=26.14&zoom=14' | jq '[.places[].id] | sort'
curl -s 'https://sloco.pp.ua/v1/map/places?swLat=44.41&swLng=26.07&neLat=44.47&neLng=26.15&zoom=14' | jq '.meta, ([.places[].id] | sort)'
```
