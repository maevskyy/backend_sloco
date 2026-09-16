# TASKS 47: Places — import address / hours / phone / website from the raw DataForSEO scrape

**Status: DONE** — mapper built, delta imported and applied 2026-08-11; verified live the
same day on 18 places sampled from `/v1/map/places` and never fetched before (so no cached
responses): `shortFormattedAddress` **18/18**, `phone` **17/18**, `openingHours` **15/18**,
`websiteUrl` **13/18**, `priceLevel` 3/18 — matching the delta's coverage table below.
iOS spec closed → `messages-to-backend-dev/done/PLACE_DETAILS_MISSING_FIELDS.md`, with
`businessStatus` reported as unavailable-at-source rather than pending.

⚠️ `GET /v1/places/{id}` is Redis-cached for an hour: any place fetched *before* the UPDATE
keeps returning nulls until the entry expires or `place:v1:*` is flushed (step 6). This is
the one thing that makes a correct import look broken.

Promotes `TBD_PLACE_DETAILS_ENRICHMENT.md` (kept for rationale/history). Closes the long
half of iOS ask `frontend_new/messages-to-backend-dev/done/PLACE_DETAILS_MISSING_FIELDS.md`.
The quick half (price backfill from `raw`, maps-uri from CID) is `TASKS_44`.

## Context

The catalog pipeline dropped the Google place-details fields, but the raw scrape it was
built from exists on Kirill's SSD
(`/Volumes/Extreme SSD/sloco/SLOCO/data_new/data/dataforseo/<city>/places.jsonl`,
DataForSEO, 2026-06-10). Verified: **every one of the 12 578 prod CIDs is present in the
raw files** (`cid` == `places.source_id` for `sloco_ai`). No Google API spend needed.

## What was built (done)

- **Mapper `scripts/integrations/dataforseo/details-delta.ts`**
  (`pnpm details:dataforseo <places.jsonl…> --out dumps/place_details_delta.csv`):
  streams the JSONL, keys by `cid`, emits
  `formatted_address` (full string), `short_formatted_address` (street + number from
  `address_info.address`), `phone` (international form), `website_url` (`url` when it is a
  real site, else `https://<domain>`; Google-Maps links filtered out),
  `price_level` (same word→0–4 map as the sloco mapper), and `opening_hours` — the
  schema's jsonb with Google-format `weekdayDescriptions`
  (`"Monday: 10:00 AM – 12:00 AM"`, `"Closed"`, `"Open 24 hours"`, multi-range
  `"10:00 AM – 3:00 PM, 5:00 PM – 11:00 PM"`; the scrape's stale `current_status` is
  deliberately dropped — the client derives open/closed from the descriptions).
- **Delta generated**: `dumps/place_details_delta.csv` — 20 054 rows (both prod cities;
  kyiv/berlin raw left untouched), 0 records without cid, 0 duplicates.
- **Coverage over the 12 578 prod places** (matched: 12 578 = 100%):

  | field | non-empty | % of prod |
  |---|---|---|
  | formatted_address | 12 575 | ~100% |
  | short_formatted_address | 12 201 | 97.0% |
  | phone | 9 142 | 72.7% |
  | opening_hours | 8 056 | 64.0% |
  | website_url | 6 139 | 48.8% |
  | price_level | 2 959 | 23.5% (same set as `TASKS_44` — redundant but harmless) |

- Checks: lint + typecheck clean; format spot-checks (Caru' cu bere, Closed days,
  Open-24-hours, multi-range days) all correct.

## Ops steps (Supabase SQL editor + Table Editor)

1. **Staging table** (a real table — the Import UI cannot target temp tables):

   ```sql
   create table if not exists public.staging_place_details (
     cid text primary key,
     formatted_address text,
     short_formatted_address text,
     phone text,
     website_url text,
     price_level integer,
     opening_hours text
   );

   alter table public.staging_place_details enable row level security;
   ```

   RLS on with no policies is this repo's convention for `public` tables
   (`profiles`, `saved_places`, `saved_collections`, `saved_collection_places`,
   `place_reactions` all do exactly this; there is no `create policy` anywhere). It keeps
   the table off the anon PostgREST surface and silences Supabase's security lint. The
   dashboard import and the UPDATE below run as the owning `postgres` role, which bypasses
   RLS, so nothing about the flow changes.

2. **Import** `dumps/place_details_delta.csv` into `staging_place_details` via the Table
   Editor (map columns by name; empty cells import as NULL).

3. **Apply** — one UPDATE, per-field NULL-guarded (never overwrites an existing value;
   idempotent):

   ```sql
   update public.places p
      set formatted_address       = coalesce(p.formatted_address, s.formatted_address),
          short_formatted_address = coalesce(p.short_formatted_address, s.short_formatted_address),
          phone                   = coalesce(p.phone, s.phone),
          website_url             = coalesce(p.website_url, s.website_url),
          price_level             = coalesce(p.price_level, s.price_level),
          opening_hours           = coalesce(p.opening_hours, s.opening_hours::jsonb)
     from public.staging_place_details s
    where p.source = 'sloco_ai'
      and p.source_id = s.cid;
   ```

   Expected: `12578 rows updated`.

4. **Verify counts**:

   ```sql
   select
     count(*) filter (where formatted_address is not null)       as addr,
     count(*) filter (where short_formatted_address is not null) as short_addr,
     count(*) filter (where phone is not null)                   as phone,
     count(*) filter (where website_url is not null)             as site,
     count(*) filter (where opening_hours is not null)           as hours,
     count(*) filter (where price_level is not null)             as price
   from public.places where source = 'sloco_ai';
   ```

   Expected ≈ `12575 / 12201 / 9142 / 6139 / 8056 / 2959`.

5. **Cleanup.** ВАЖНО: СЛЕДУЮЩАЯ КОМАНДА — `DROP TABLE`. ОНА УДАЛЯЕТ ТОЛЬКО
   STAGING-ТАБЛИЦУ `public.staging_place_details` (НЕ продовые данные и НЕ `public.places`):

   ```sql
   drop table public.staging_place_details;
   ```

6. **Flush the place-details Redis cache** (server):

   ```bash
   cd /opt/backend_sloco && docker compose exec redis sh -c "redis-cli --scan --pattern 'place:v1:*' | xargs -r redis-cli DEL"
   ```

## Live acceptance (the iOS spec's own)

`curl -s https://sloco.pp.ua/v1/places/12474 | jq '.place | {openingHours, shortFormattedAddress, priceLevel, googleMapsUri}'`
returns non-null values where the source has them; a ~20-place spot check shows coverage in
line with the table above. Then: update the field table in the iOS spec, flip its status,
`git mv` to `done/` — and iOS deletes the placeholder address/hours in `PlaceCardView`.

## Out of scope

- `business_status` — **absent from the DataForSEO scrape** (top-level and `raw` both);
  tell iOS it stays empty until some future source provides it.
- `international_phone` — the scrape carries one phone (international form) → stored in
  `phone` only.
- The real `ChIJ…` `place_id` (present in the scrape, 100%) — `public.places` has no column
  for it; adding one is a separate decision (migration) if exact Maps deep-links ever need
  it — the CID link works today.
- Kyiv/Berlin raw data — scraped but not in the catalog/prod; a future city-expansion task.
- Freshness: scraped 2026-06-10 — "present when known", per the iOS spec's own expectation.
