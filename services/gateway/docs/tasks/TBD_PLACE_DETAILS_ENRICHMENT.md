# TBD: Place details enrichment — hours, addresses, phone, website, business status

**Status: SUPERSEDED by `TASKS_47_PLACE_DETAILS_IMPORT.md` (2026-08-11)** — the fields were
found in the local raw DataForSEO scrape (see the Finding below), so the external-data
blocker dissolved; kept for rationale/decision history.

The long half of iOS ask
`frontend_new/messages-to-backend-dev/not-done/PLACE_DETAILS_MISSING_FIELDS.md`. The in-repo
quick wins (`price_level`, `google_maps_uri`) are `TASKS_44_PLACE_DATA_BACKFILLS.md`.

## What is established (so nobody re-investigates)

The spec's central question was *"serializer drop, or never ingested?"* Audited 2026-08-11:

- **The read path is clean end-to-end.** Migration `009` declares `formatted_address`,
  `short_formatted_address`, `business_status`, `phone`, `international_phone`,
  `website_url`, `opening_hours jsonb`; `place_details_by_id` (and the map/search/feed RPCs
  where applicable) select them; `places.mappers.ts` maps them 1:1. Nothing is dropped.
- **The source catalog never had these fields.** `locations_combined_food_ttd.csv` (12 578
  rows, 60 columns, `services/recommendation/artifacts/`) contains **no** address / hours /
  phone / website / business-status columns, and the sloco mapper's `SLOCO_COLUMNS`
  accordingly doesn't emit them. This is a data-acquisition gap, not a code bug.
- `serves` / `features`: present as columns but **0 / 12 578 non-empty in the source** — the
  always-`{}` objects iOS sees are "reserved", not lost (answer this in the spec file).
- The join key is solid: `source_id` for `sloco_ai` is the numeric **Google CID** — any
  enrichment can be delivered and applied keyed by CID.

## The decision to make (owner: Kirill + data team)

> **Finding (2026-08-11): the fields already exist locally — no Google API spend needed.**
> Kirill's copy of the data-team repo (`/Volumes/Extreme SSD/sloco/SLOCO`) contains the raw
> DataForSEO scrape the catalog was built from
> (`data_new/data/dataforseo/<city>/places.jsonl`, scraped 2026-06-10, **4 cities** —
> bucharest 100 MB, tbilisi 72 MB, plus unprocessed kyiv 194 MB and berlin 320 MB).
> Measured on 500 Bucharest records: `address`/`address_info` **100%**, `work_time` (hours)
> **98%**, `phone` **95%**, `url`/`domain` (website) **93%**, `price_level` 52%. Every
> record carries the **`cid`** (= prod `source_id`, a trivial join) **and the real `ChIJ…`
> `place_id`** (which the client once wanted for exact Maps links and we thought
> unavailable). The enrichment therefore reduces to an offline mapper over these files —
> option 2 below is dead.

Two ways to obtain the fields, not mutually exclusive:

1. **Build the delta from the local raw scrape** (preferred, free): a mapper
   `places.jsonl → delta CSV keyed by cid` (address, short form from `address_info`, phone,
   website from `url`/`domain`, and `work_time` → the schema's `openingHours` jsonb with
   Google-format `weekdayDescriptions`), then a `TASKS_44`-style NULL-guarded UPDATE
   import. Freshness caveat: scraped 2026-06-10 — "present when known" per the iOS spec's
   own expectation.
2. ~~Backend one-off fetch via the Google Places Details API~~ — unnecessary, the data
   exists locally (kept for history: it would have been a bounded one-time cost).

Priority order (from the iOS spec, by user impact): `openingHours` →
`shortFormattedAddress`/`formattedAddress` → `businessStatus` → `phone`/`websiteUrl`.
Coverage expectation: "present when known", not 100% — iOS handles absence per field.

## Delivery shape (when the data exists)

- **UPDATE-style import keyed by `source_id`** (temp table + NULL-guarded UPDATE, the
  `TASKS_44` pattern) — NOT the truncate-and-reimport flow; user data (`saved_places`,
  reactions) and photo links must survive.
- Extend `scripts/integrations/sloco/map.ts` `SLOCO_COLUMNS` with the new fields so future
  full imports carry them natively.
- `opening_hours` lands as the schema's existing jsonb shape
  (`{openNow?, weekdayDescriptions?, nextCloseTime?}`), `weekdayDescriptions` in the Google
  format iOS already parses (`"Monday: 9:00 AM – 11:00 PM"`, `"Closed"`, `"Open 24 hours"`).
- Flush the place-details Redis cache after the import (`TASKS_30` flow).

## Acceptance (the iOS spec's own)

`curl /v1/places/12474` returns non-null
`openingHours / shortFormattedAddress / priceLevel / googleMapsUri` for places that have them
in Google; a ~20-place spot check shows coverage in line with Google. iOS then deletes the
placeholder address/hours strings in `PlaceCardView` (their tracked task) and the card can
finally answer "is it open now".

## Promote to a numbered task when

The data source decision is made (re-export vs API fetch) — then this becomes
`TASKS_NN_PLACE_DETAILS_IMPORT.md` with the concrete pipeline, counts, and test plan.
