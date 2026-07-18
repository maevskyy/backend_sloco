# TASKS 36: Place Details — Enumerated Photo List

**Status: Planned (awaiting approval).**

Frontend request (iOS, item 1 of 3): the fullscreen photo gallery is blocked
because `GET /v1/places/:id` returns only **one** photo (`primaryPhoto`). The
client needs an ordered list of direct photo URLs for the same place.

Gateway-only. No migration. No recommendation-service change. Independent of
`TASKS_37`.

## Context

`place_photos` (migration `008`) is a plain one-to-many table holding **every**
photo per place, keyed by `(place_source, place_source_id)` and orderable
best-first by `photo_index`. There is an index for exactly this lookup
(`place_photos_place_idx on (place_source, place_source_id)`). Its `public_url` is
the **direct R2 URL** written at index time — no Supabase transform (the transform
endpoint returns `403 FeatureNotEnabled` on this tenant, which is the constraint
the frontend called out). So the data exists; the API just does not expose it.

Today the RPC `place_details_by_id` ([migration `010`](../../supabase/migrations/010_map_pins_place_details_rpc.sql))
joins `place_photos` for a **single** hero photo and the mapper turns that into
`primaryPhoto`. `photoDetails` is an opaque JSON passthrough of `p.photo_details`
and is **not** the enumerated-URL contract the frontend needs.

## Decisions

**Option A — add `photos[]` to `PlaceDetails`** (chosen over a separate
`GET /v1/places/:id/photos` endpoint): the gallery is always opened from the
place-details screen, which already fetches `GET /v1/places/:id`. One response, one
cache entry (details are already Redis-cached, TTL 3600s), no new route/auth
surface.

**Fill it with a second store query — no migration.** The details row already
gives us `source` + `source_id`, so `photos[]` is a second, indexed `select` on
`place_photos`. We deliberately do **not** fold the list into the
`place_details_by_id` RPC: that would only save one round trip (which happens on a
Redis **miss** only), at the cost of a `DROP + CREATE` of a large SQL function —
not worth it.

- `photos[]` reuses the existing `PlacePrimaryPhoto` shape
  (`{ path, url, width, height, source }`), ordered **best-first** by `photo_index`.
- `primaryPhoto` is **unchanged** (backward-compatible hero). `photos[0]` is the
  best gallery photo and in practice coincides with the hero; the contract keeps
  the two independent rather than asserting `primaryPhoto === photos[0]`.
- **Capped at 20** (frontend: "12–20 is plenty"), so the payload stays bounded.
  `totalPhotoCount` may exceed `photos.length` — expected, already surfaced by the
  count fields.
- `photos` is **not** user-specific, so it caches with the rest of the details
  object — no change to the `CachedPlaceDetails` `Omit` in `places.service.ts`.

## Changes (`services/gateway`)

1. **`src/modules/places/stores/places.store.ts`** — new method
   `placePhotos(source, sourceId)`:
   ```ts
   getSupabaseClient()
     .from("place_photos")
     .select("storage_path, public_url, width, height, photo_source")
     .eq("place_source", source)
     .eq("place_source_id", sourceId)
     .order("photo_index", { ascending: true, nullsFirst: false })
     .order("id", { ascending: true })
     .limit(20)
   ```
   Add it to `PlacesStoreContract`; add a `PlacePhotoRow` type.

2. **`src/modules/places/services/places.service.ts`** — on **cache miss** only,
   after `store.placeDetailsById(placeId)` returns the row, call
   `store.placePhotos(row.source, row.source_id)` and pass the result into the
   mapper. Cache the composed object (photos included), so a cache **hit** serves
   `photos[]` with **no** extra query.

3. **`src/modules/places/common/places.schemas.ts`** — add
   `photos: z.array(placePrimaryPhotoSchema)` to `placeDetailsSchema` (reuse the
   already-registered `PlacePrimaryPhoto`; the `PlaceDetails` component regenerates
   automatically — no new registration).

4. **`src/modules/places/common/places.mappers.ts`** — `mapPlaceDetailRow(row,
   photos)` maps each `place_photos` row to `{ path, url, width, height, source }`.
   `primaryPhoto` mapping unchanged; defaults to `[]` when there are no photos.

5. **`src/modules/places/common/places.types.ts`** — `photos` is inferred into
   `PlaceDetails` via the schema; add the `PlacePhotoRow` store type.

6. **Docs** — note the `photos[]` field on the place-details contract in
   `docs/CURRENT_STATE.md` / `docs/DECISIONS.md`; it also appears in the generated
   OpenAPI at `/v1/swagger/openapi.json`.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- Store: a place with several `place_photos` rows returns them best-first by
  `photo_index`; a place with none returns `[]`; each element carries the
  **direct** `public_url` (no transform querystring); capped at 20.
- Service: `photos[]` present on a cold (miss) response **and** on a warm (hit)
  response, with **no** second `place_photos` query on the hit.
- Mapper: `primaryPhoto` and all existing fields unchanged; `photos` defaults to
  `[]`.
- OpenAPI shows `photos` on `PlaceDetails`.

## Dependencies

- **Upstream:** none — the photo data is already imported.
- **Downstream:** none.

## Out Of Scope

Folding photos into the `place_details_by_id` RPC (rejected — not worth a
migration); a separate `/photos` endpoint (Option B); pagination of photos; the
`googlePlaceId` appendix (item 3 — not derivable from current data, dropped); any
feed change (`TASKS_37`); any recommendation-service change.
