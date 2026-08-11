# TASKS 44: Places — `price_level` + `google_maps_uri` backfills (no external data)

**Status: DONE** — root cause found, code fixed and both backfills applied 2026-08-11.
Verified live the same day: `googleMapsUri` **18/18** on a never-cached sample;
`priceLevel` **107/400** on `/v1/map/places` (≈27%, in line with the source's 23.5%) and
3/18 on the detail sample. Note for whoever reads a stale response: `GET /v1/places/{id}`
is Redis-cached for an hour, so entries fetched *before* the UPDATE keep showing nulls
until they expire or `place:v1:*` is flushed.

The two quick wins split out of iOS ask
`frontend_new/messages-to-backend-dev/done/PLACE_DETAILS_MISSING_FIELDS.md` (0/30 sampled
places carry `priceLevel`; 0/30 carry `googleMapsUri`). The fields that genuinely need new
external data (hours, addresses, phone, website, business status) are
`TBD_PLACE_DETAILS_ENRICHMENT.md`. Umbrella plan: `../../../../ios-asks-implementation-plan.md`
§3.

## Context (verified in code + data)

- The read path is clean end-to-end: columns exist (migration `009`), every RPC selects them,
  `places.mappers.ts` maps them 1:1. The nulls come from the data, not the serializer.
- **`price_level` IS in the source catalog** — measured
  `services/recommendation/artifacts/locations_combined_food_ttd.csv`: **2 959 / 12 578 rows
  non-empty (23.5%)**. ~~Import mishap suspected~~ → **real root cause found (2026-08-11):
  the source values are CATEGORICAL strings** (`inexpensive` 1 763 / `moderate` 1 125 /
  `expensive` 69 / `very_expensive` 2), while the mapper parsed the column with
  `optionalNumberCell` → every word became an empty cell → NULL. Confirmed end-to-end
  against prod: Restaurant Slow (id 4096), SIPPERS (9139), Namaste (5032) are `moderate` at
  the source and `priceLevel: null` in the live API. The words map 1:1 onto Google's
  integer semantics: `free→0, inexpensive→1, moderate→2, expensive→3, very_expensive→4`.
  Bonus: the mapper preserves the full source row in the `raw` jsonb column, so if `raw`
  was mapped during the original import, **the backfill needs no CSV at all** —
  `raw->>'price_level'` is already sitting in the database (path A below).
- **`google_maps_uri` needs no data at all**: for `source = 'sloco_ai'`, `source_id` is the
  numeric Google CID, and `https://maps.google.com/?cid=<source_id>` is the canonical place
  link (the iOS app already builds exactly this client-side from `sourceId`).
- Place details are Redis-cached for 1h (`PLACE_CACHE_TTL_SECONDS`, `TASKS_30`) — data-only
  changes need a cache flush or a TTL wait to become visible.

## Decisions

- Both backfills are **idempotent, NULL-guarded `UPDATE`s** — they only fill missing values,
  never overwrite (`where … is null`). No migration: data ops, not schema (Migration
  Restraint Rule — nothing about Postgres itself changes).
- `price_level` values come from the CSV already committed in this repo (no new handoff
  needed); joined by `source_id` via a temp table.
- The sloco mapper additionally learns to emit `google_maps_uri` (synthesized from
  `place_id`) so future imports carry it without a backfill. (`price_level` is already
  mapped.)

## Changes

1. **Probe which path applies** (Supabase SQL editor):

   ```sql
   select
     count(*) filter (where price_level is not null)        as price_filled,
     count(*) filter (where raw->>'price_level' is not null) as raw_has_price,
     count(*)                                               as total
   from public.places where source = 'sloco_ai';
   ```

   Expected: `price_filled ≈ 0`, `raw_has_price ≈ 2959`. Record the result here.

   > **Probe result (2026-08-11, Kirill, prod):** `price_filled: 0`,
   > `raw_has_price: 2959`, `total: 12578` — exactly as predicted. **Path A applies;
   > path B / the CSV stays unused.**

2. **`price_level` backfill — path A (raw jsonb is populated; no CSV needed):**

   ```sql
   update public.places p
      set price_level = case p.raw->>'price_level'
        when 'free'           then 0
        when 'inexpensive'    then 1
        when 'moderate'       then 2
        when 'expensive'      then 3
        when 'very_expensive' then 4
      end
    where p.source = 'sloco_ai'
      and p.price_level is null
      and p.raw->>'price_level' in
        ('free','inexpensive','moderate','expensive','very_expensive');
   ```

   **Path B (only if `raw_has_price` is 0):** import `dumps/price_level_backfill.csv`
   (2 959 `source_id,price_level` rows, already integer-mapped) into a temp table via the
   Supabase Import UI, then:

   ```sql
   update public.places p
      set price_level = t.price_level
     from tmp_price_level t
    where p.source = 'sloco_ai' and p.source_id = t.source_id
      and p.price_level is null;
   ```

3. **`google_maps_uri` backfill:**

   ```sql
   update public.places
      set google_maps_uri = 'https://maps.google.com/?cid=' || source_id
    where source = 'sloco_ai' and google_maps_uri is null;
   ```

4. **Mapper** — `scripts/integrations/sloco/map.ts`: translate the categorical
   `price_level` words → integers (numeric passthrough kept) and emit a synthesized
   `google_maps_uri` column, so future imports carry both natively.
5. **Flush the place-details Redis cache** (the manual import-flush flow from `TASKS_30`) or
   accept the 1h TTL.
6. Docs: note both fields as populated in `docs/FRONTEND_MAP_API.md` /
   `docs/FRONTEND_FEED_API.md` where they appear; update the iOS spec file's field table
   (price ✓, maps-uri ✓) and answer its `serves`/`features` question (empty at the source —
   reserved, 0/12 578).

## Addendum — implemented 2026-08-11 (code half)

- Mapper fixed and verified against the real catalog: 12 578 rows out,
  `price_level` now integer (1 763×1, 1 125×2, 69×3, 2×4 — matches the source
  distribution exactly), `google_maps_uri` on 12 578/12 578.
- `dumps/price_level_backfill.csv` generated (path B fallback) and documented in
  `dumps/README.md`.
- Live pre-check recorded in Context (3 sampled places: `moderate` at source, null in API).
- Remaining: steps 1–3 (Supabase, ops), 5 (cache flush), 6 (docs + iOS spec — after the
  live verification).

Note: both UPDATEs touch up to ~12.5k rows. They are not in the destructive-SQL list (no
DROP/TRUNCATE/DELETE/ALTER) and are NULL-guarded, but run the counts before/after and keep
the temp table until verified.

## Test Plan

- Before/after counts for both columns; spot-check 5 known CIDs
  (`curl /v1/places/{id} | jq '.place | {priceLevel, googleMapsUri}'`).
- Live coverage roughly matches the source (~23% overall for price; 100% maps-uri for
  `sloco_ai`).
- `pnpm build && pnpm test && pnpm lint` for the mapper change; `pnpm map:sloco` dry run
  emits the new column.
- iOS side after ship: the card's `$$` renders from real data; "Route" opens the place card
  via `googleMapsUri` (their tracked placeholder-deletion tasks).

## Dependencies

- None.

## Out Of Scope

- Hours / addresses / phone / website / business status (`TBD_PLACE_DETAILS_ENRICHMENT.md` —
  external data).
- `price_min_ron`/`price_max_ron` (0/12 578 in the source — nothing to backfill).
- Any schema change.
