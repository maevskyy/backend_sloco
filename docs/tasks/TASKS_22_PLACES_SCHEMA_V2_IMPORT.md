# TASKS 22: Places Schema V2 Import

## Summary

Replace the old MVP `public.places` shape with a richer product-ready `places`
schema based on the new `locations.csv`.

Current dataset:

```text
/Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446/locations.csv
```

Latest observed shape:

```text
rows: 2508
columns: 119
latitude: present for all rows
longitude: present for all rows
```

This is no longer a raw provider dump. It is an enriched domain dataset with:

- Google identity/details;
- coordinates and address;
- contact links;
- ratings and review stats;
- AI summaries;
- taste axes;
- map visibility scores;
- photo references.

We should migrate `places` to this shape instead of forcing the dataset through
the old generic integration mapper.

## Current Status

```text
Planned
```

`TASKS_21_PLACE_PHOTOS_INTEGRATION.md` already creates `place_photos` and uploads
photo files by stable Google `place_id`.

This task should import places with:

```text
source = 'google'
source_id = locations.place_id
```

That keeps photo joins stable:

```text
place_photos.place_source = places.source
place_photos.place_source_id = places.source_id
```

## Goals

- Replace old `public.places` data with the new `locations.csv`.
- Keep the table name `public.places`.
- Keep PostGIS bbox querying.
- Add typed columns for important product fields.
- Keep raw provider/details blobs in JSONB where typing every field is not worth it.
- Update `places_in_bbox` to use new ranking fields.
- Update backend map store/types/mappers/schema.
- Preserve response-level `displayKind` and `displayPriority`.
- Keep `place_photos` compatible through `source/source_id`.

## Non-Goals

- Do not redesign saved places.
- Do not import user-generated photos.
- Do not make a place detail endpoint yet.
- Do not expose all 119 fields to the frontend.
- Do not proxy photo bytes through backend.
- Do not add personalization scoring in this task.

## Current `places` Shape

Current table is intentionally small:

```text
id
source
source_id
name
country
city
category
latitude
longitude
geom
rating
price_level
reviews_count
embedding_text
attributes
raw
fetched_at
created_at
```

This was good for MVP imports from multiple providers. It is now too generic for
the enriched Google/AI dataset.

## New `locations.csv` Shape

Important columns:

```text
place_id
name
latitude
longitude
formatted_address
business_status
google_maps_uri
details_nationalPhoneNumber
details_internationalPhoneNumber
details_websiteUri
primary_type
types
google_rating
google_user_rating_count
apify_review_count
apify_rating_avg
price_level
price_min_ron
price_max_ron
details_regularOpeningHours
serves
features
ai_card_summary
ai_place_type_summary
ai_vibe
ai_what_to_expect
ai_food_and_drinks
ai_price
ai_service
ai_the_move
ai_watch_out
ai_tags
ai_tags_csv
ai_tags_json
axis_quiet_lively
axis_work_social
axis_day_night
axis_casual_premium
axis_drinks_food
axis_local_tourist
axis_cheap_expensive
axis_traditional_experimental
ai_confidence
rating_count_for_score
bayesian_rating
rating_score_0_100
popularity_score_0_100
rating_confidence_0_100
map_visibility_score
map_visibility_rank
map_min_zoom_global
review_photo_count
vibe_photo_count
total_photo_count
primary_photo_file
```

## Schema Strategy

Use a hybrid model:

1. Typed columns for fields we filter, rank, show, or join on.
2. JSONB columns for large provider/detail blobs.
3. `raw jsonb` for the full original CSV row.

Do **not** create 119 typed columns. That would make the table harder to reason
about and painful to evolve.

## Proposed `public.places` V2 Columns

Identity:

```sql
id bigserial primary key
source text not null
source_id text not null
name text not null
country text not null default 'romania'
city text not null default 'bucharest'
category text not null default 'others'
primary_type text
types text[] not null default '{}'
business_status text
```

Geo/address:

```sql
latitude double precision not null
longitude double precision not null
geom geometry(Point, 4326) generated always as (...) stored
formatted_address text
short_formatted_address text
```

Contact/links:

```sql
google_maps_uri text
phone text
international_phone text
website_url text
```

Ratings/reviews:

```sql
rating numeric
reviews_count integer
google_rating numeric
google_user_rating_count integer
apify_review_count integer
apify_rating_avg numeric
rating_count_for_score integer
bayesian_rating numeric
rating_score_0_100 numeric
popularity_score_0_100 numeric
rating_confidence_0_100 numeric
```

Price:

```sql
price_level smallint
price_min_ron numeric
price_max_ron numeric
```

AI/taste:

```sql
ai_card_summary text
ai_place_type_summary text
ai_vibe text
ai_what_to_expect text
ai_food_and_drinks text
ai_price text
ai_service text
ai_the_move text
ai_watch_out text
ai_tags text[] not null default '{}'
ai_tags_json jsonb not null default '[]'
ai_confidence numeric
axis_quiet_lively smallint
axis_work_social smallint
axis_day_night smallint
axis_casual_premium smallint
axis_drinks_food smallint
axis_local_tourist smallint
axis_cheap_expensive smallint
axis_traditional_experimental smallint
```

Map visibility:

```sql
map_visibility_score numeric not null default 0
map_visibility_rank integer
map_min_zoom_global smallint
```

Photo refs:

```sql
review_photo_count integer not null default 0
vibe_photo_count integer not null default 0
total_photo_count integer not null default 0
primary_photo_path text
```

JSONB detail buckets:

```sql
opening_hours jsonb
serves jsonb not null default '[]'
features jsonb not null default '{}'
google_details jsonb not null default '{}'
apify_details jsonb not null default '{}'
ai_details jsonb not null default '{}'
photo_details jsonb not null default '{}'
raw jsonb not null default '{}'
```

Timestamps:

```sql
fetched_at timestamptz
created_at timestamptz not null default now()
updated_at timestamptz not null default now()
```

Constraints:

```sql
unique (source, source_id)
rating between 0 and 5
google_rating between 0 and 5
apify_rating_avg between 0 and 5
price_level between 0 and 4
reviews_count >= 0
photo counts >= 0
axis values between 0 and 100
```

## What Not To Store In DB

Do not store response-only map display fields:

```text
displayKind
displayPriority
```

These are computed in backend service code from:

- current viewport/zoom;
- density settings;
- ranking result order.

DB stores ranking inputs:

```text
map_visibility_score
map_visibility_rank
map_min_zoom_global
rating_score_0_100
popularity_score_0_100
```

Backend response still returns:

```json
{
  "displayKind": "featured",
  "displayPriority": 1
}
```

## Migration Strategy

Because current DB data is disposable test data, use destructive migration for
`places` only.

Important:

- Do not drop `saved_places`, `saved_collections`, or `place_photos`.
- Dropping `places` may leave saved places pointing at old `places.id`.
- For MVP/dev this is acceptable only if we also clear saved places or accept
  broken old saved references.

Recommended migration order:

1. Drop/replace `places_in_bbox`.
2. Drop old `public.places`.
3. Create new `public.places`.
4. Recreate indexes.
5. Recreate `places_in_bbox`.
6. Import new locations.
7. Verify photo join through `source/source_id`.

Migration file:

```text
supabase/migrations/009_recreate_places_v2.sql
```

## `places_in_bbox` V2

The RPC should keep the same name:

```sql
public.places_in_bbox(sw_lat, sw_lng, ne_lat, ne_lng, result_limit)
```

Return fields needed by map endpoint:

```text
id
source
source_id
name
country
city
category
primary_type
types
latitude
longitude
rating
price_level
reviews_count
google_rating
google_user_rating_count
apify_review_count
apify_rating_avg
map_visibility_score
map_visibility_rank
map_min_zoom_global
ai_card_summary
ai_tags
primary_photo_path
total_photo_count
attributes/details if still needed temporarily
```

Sort strategy:

```sql
order by
  p.map_visibility_score desc,
  p.rating_score_0_100 desc nulls last,
  p.popularity_score_0_100 desc nulls last,
  p.google_rating desc nulls last,
  p.google_user_rating_count desc nulls last,
  p.id asc
```

Optional zoom optimization:

```sql
and (
  requested_zoom is null
  or p.map_min_zoom_global is null
  or p.map_min_zoom_global <= requested_zoom
)
```

Do not add zoom filtering until we intentionally update the backend query
contract. Current backend filtering/density already works without it.

## Import Strategy

Do not use old generic integration mappers as the primary path.

Add a dedicated script:

```text
scripts/places/import-locations-v2.ts
```

Recommended behavior:

- read `locations.csv`;
- validate required fields:
  - `place_id`;
  - `name`;
  - `latitude`;
  - `longitude`;
- normalize Python-list-like columns into arrays:
  - `types`;
  - `serves`;
  - `ai_tags`;
- parse JSON columns:
  - `details_regularOpeningHours`;
  - `details_googleMapsLinks`;
  - `details_addressComponents`;
  - `details_paymentOptions`;
  - `details_accessibilityOptions`;
  - `ai_tags_json`;
  - `ai_summary_json`;
- emit normalized CSV for Supabase Table Editor or upsert directly.

Preferred for MVP:

```text
direct Supabase upsert in batches
```

Why:

- easier to rerun;
- no manual CSV mapping for 70+ columns;
- can validate parse errors before writing.

CLI:

```bash
pnpm places:import /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446/locations.csv --dry-run --limit 50
pnpm places:import /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446/locations.csv --limit 50
pnpm places:import /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446/locations.csv
```

## Backend Changes

Update map module:

```text
src/modules/map/common/map.types.ts
src/modules/map/common/map.schemas.ts
src/modules/map/common/map.mappers.ts
src/modules/map/common/map.ranking.ts
src/modules/map/stores/map.store.ts
src/modules/map/tests/*
```

`PlaceRow` should include new DB fields.

`MapPlace` response can grow carefully:

```json
{
  "id": 123,
  "source": "google",
  "sourceId": "ChIJ...",
  "name": "Seneca Anticafe",
  "country": "romania",
  "city": "bucharest",
  "latitude": 44.4584793,
  "longitude": 26.0787248,
  "rating": 4.8,
  "priceLevel": null,
  "numberOfReviews": 1411,
  "primaryType": "book_store",
  "aiCardSummary": "...",
  "aiTags": ["cozy", "quiet"],
  "mapVisibilityScore": 89.9,
  "primaryPhoto": {
    "path": "photos/reviews/...",
    "url": "https://...",
    "source": "review"
  },
  "isSaved": false,
  "savedCollectionIds": [],
  "displayKind": "featured",
  "displayPriority": 1
}
```

Keep map response lightweight. Do not add full opening hours, all AI fields, or
all photo files to map pins.

## Photo Integration

`locations.csv.primary_photo_file` uses source bundle paths:

```text
photos/reviews/<place_id>/<file>.jpg
photos/vibes/<place_id>/<file>.jpg
```

`place_photos.storage_path` uses app storage paths:

```text
google/<place_id>/<photo_source>/<photo_item_id>.jpg
```

To return `primaryPhoto`, backend should join or lookup photo metadata by:

```text
places.source = place_photos.place_source
places.source_id = place_photos.place_source_id
```

Primary photo selection order:

1. If `primary_photo_path` can be mapped to `place_photos`, use it.
2. Else use first `vibe` photo by `photo_index`.
3. Else use first `review` photo by `photo_index`.
4. Else `primaryPhoto = null`.

Do not block places import on perfect photo selection. We can first import
places and add `primaryPhoto` to API in a follow-up sub-step.

## Validation Queries

After import:

```sql
select count(*) from public.places;

select
  count(*) filter (where latitude is null or longitude is null) as missing_geo,
  count(*) filter (where source_id is null or source_id = '') as missing_source_id
from public.places;

select min(latitude), max(latitude), min(longitude), max(longitude)
from public.places;

select count(*)
from public.place_photos ph
join public.places p
  on p.source = ph.place_source
 and p.source_id = ph.place_source_id;
```

Expected:

```text
places count = 2508
missing_geo = 0
photo join count > 0
```

Map RPC check:

```sql
select *
from public.places_in_bbox(
  44.30,
  25.90,
  44.60,
  26.30,
  100
)
limit 5;
```

## Test Plan

Code:

```bash
pnpm build
pnpm test
pnpm lint
```

Import dry-run:

```bash
pnpm places:import /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446/locations.csv --dry-run --limit 50
```

Local/API smoke after deploy:

```bash
curl https://sloco.pp.ua/v1/health
curl "https://sloco.pp.ua/v1/map/places?swLat=44.30&swLng=25.90&neLat=44.60&neLng=26.30&zoom=13&limit=100"
```

OpenAPI:

```bash
curl https://sloco.pp.ua/v1/swagger/openapi.json
```

## Acceptance Criteria

- New `public.places` schema exists.
- Old test place data is replaced by 2508 new rows.
- `places.source='google'` and `places.source_id=locations.place_id`.
- `places_in_bbox` returns new places in Bucharest bbox.
- Map endpoint still returns:
  - `displayKind`;
  - `displayPriority`;
  - saved state fields.
- Photo metadata can join to places by `source/source_id`.
- CI is green.

