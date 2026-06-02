# TASKS 29: Frontend-owned map pin tiers

Status: Done.

## Summary

After `TASKS_28`, the backend made map pin membership stable with
`map_visibility_score >= minScore(zoom)`. We initially also returned
`displayKind: "featured" | "dot"` and `featuredMinScore`, but this UI tiering
is awkward for frontend rendering.

Decision: backend owns **which pins are eligible and their stable order**.
Frontend owns **how pins look**.

## Changes

- Remove `displayKind` from `MapPlacePin`.
- Remove `featuredMinScore` from `MapPlacesMeta`.
- Keep `displayPriority` as stable 1-based ordering for z-index, tap priority,
  and frontend-owned marker tiering.
- Keep `mapVisibilityScore` so frontend can combine backend quality signal with
  current zoom and local rendering constraints.
- Keep `minScore`, `safetyCap`, and `capHit` for debug and calibration.

## Contract

`GET /v1/map/places` still returns lightweight pins:

```json
{
  "id": 1,
  "name": "Pane e Vino",
  "latitude": 52.552578,
  "longitude": 13.352883,
  "mapVisibilityScore": 91,
  "isSaved": false,
  "displayPriority": 1
}
```

The frontend can choose, for example:

- first N by `displayPriority` as large markers;
- remaining pins as small points;
- or a zoom-aware mix using `mapVisibilityScore`.

Backend does not send that choice anymore.

## Files

```text
src/modules/map/common/map.schemas.ts
src/modules/map/common/map.ranking.ts
src/modules/map/common/map.mappers.ts
src/modules/map/services/map.service.ts
src/modules/map/controllers/map.controller.ts
src/modules/map/tests/map.mappers.test.ts
src/modules/map/tests/map.ranking.test.ts
src/modules/map/tests/map.routes.test.ts
src/modules/map/tests/map.service.test.ts
docs/FRONTEND_MAP_API.md
docs/FRONTEND_MAP_PINS_STABILITY.md
```

## Verification

- `pnpm typecheck`
- `pnpm test`
- `pnpm lint`
- `pnpm build`
