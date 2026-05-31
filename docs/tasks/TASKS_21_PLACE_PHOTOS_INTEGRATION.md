# TASKS 21: Place Photos Storage Integration

## Summary

Start importing the new place photo dataset into Supabase Storage and Postgres
metadata tables.

Current dataset:

```text
/Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446
```

Important files:

```text
locations.csv
photo_manifest.csv
photo_manifest.jsonl
metadata.json
photos/reviews/
photos/vibes/
```

Current dataset stats from `metadata.json`:

```text
locations_count: 2508
photo_count: 65409
review photos: 59237
vibe photos: 6172
total photo bytes: about 21 GB
places_with_photos: 1929
```

Supabase Pro is already bought, so storage capacity should be enough for the
first full upload. We still start with a sample upload and dry-run flow because
65k files is enough to punish sloppy scripts.

## Current Status

```text
In progress
```

The new `locations.csv` currently does **not** contain latitude/longitude. A new
export will be provided later. This task does not block on that export because
photo relations use the stable Google `place_id`.

Stable join key:

```text
locations.csv.place_id = photo_manifest.csv.place_id = future places.source_id
```

Do not join photos to `places.id` yet. `places.id` is an internal database ID
and can change when we wipe/reimport places.

Implemented in repo:

```text
supabase/migrations/008_create_place_photos.sql
scripts/photos/upload-place-photos.ts
pnpm photos:upload
```

Verified dry-run:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --dry-run --limit 100
```

Result:

```text
100 manifest rows checked
0 missing files
```

Manual gate before real upload:

```text
Apply supabase/migrations/008_create_place_photos.sql in Supabase.
```

## Goals

- Create Supabase Storage bucket for place photos.
- Create a Postgres metadata table for place photos.
- Upload photos from the local dataset into Supabase Storage.
- Import metadata from `photo_manifest.csv`.
- Keep the flow idempotent and resumable.
- Prepare backend to later return `primaryPhoto` in map/details responses.

## Non-Goals

- Do not update `public.places` in this task.
- Do not import the new `locations.csv` yet.
- Do not add photos to the map endpoint yet.
- Do not build image transformations yet.
- Do not proxy photo bytes through Fastify or Nginx.
- Do not make user-generated photo uploads.

## Storage Model

Use Supabase Storage for image files.

Recommended bucket:

```text
place-photos
```

For MVP, make the bucket public so the iOS app can load images directly from
Supabase CDN URLs.

Backend responsibility:

```text
decide which photo URL to return
```

Storage/CDN responsibility:

```text
serve actual image bytes
```

Do not serve 21 GB of photos through our Hetzner backend.

## Storage Path Strategy

Use deterministic paths based on the stable Google place ID.

Recommended path:

```text
google/{place_id}/{photo_source}/{photo_item_id}.{ext}
```

Examples:

```text
google/ChIJ_xaqNQMCskAR8aQ8oHos1Ro/review/CIABIhAxcZlAuKmdnsaZs_BBDn7m.jpg
google/ChIJaX6OBQD_sUARpsOX3IMt2R4/vibe/0x40b1ff00058e7e69_0x1ed92d83dc97c3a6_CIHM0ogKEICAgIC3j8LT2gE.jpg
```

Why not keep the raw local path exactly?

```text
photos/reviews/<place_id>/<file>
```

That path is fine as source data, but the storage path should be an app-owned
namespace. It makes later provider imports easier:

```text
google/...
osm/...
manual/...
```

## Database Model

Create a photo metadata table. The metadata table is not optional: Storage knows
that files exist, but Postgres tells the backend what each file means.

Relationship:

```text
one place -> many photos
one photo -> one place
```

Photo rows know their place:

```text
place_photos.place_source_id -> places.source_id
```

At first, do not add a hard foreign key to `places`, because we are about to
wipe and reimport places. Add the FK later after the new places table is stable.

Proposed table:

```sql
create table if not exists public.place_photos (
  id bigserial primary key,

  place_source text not null default 'google',
  place_source_id text not null,

  photo_source text not null,
  photo_item_id text not null,

  storage_bucket text not null default 'place-photos',
  storage_path text not null,
  public_url text,

  source_url text,
  original_file text,

  bytes integer,
  content_type text,
  width integer,
  height integer,

  review_id text,
  review_rating numeric,
  review_published_at timestamptz,
  review_language text,

  author_id text,
  author_name text,

  vibe_place_id text,
  category text,
  category_label text,
  uploaded_by_owner boolean,
  upload_date timestamptz,
  photo_index integer,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_photos_source_chk
    check (photo_source in ('review', 'vibe')),
  constraint place_photos_unique_source_photo
    unique (place_source, place_source_id, photo_source, photo_item_id),
  constraint place_photos_unique_storage_path
    unique (storage_bucket, storage_path)
);

create index if not exists place_photos_place_idx
  on public.place_photos (place_source, place_source_id);

create index if not exists place_photos_primary_lookup_idx
  on public.place_photos (place_source, place_source_id, photo_source, photo_index);
```

Potential later optimization:

```text
places.primary_photo_id
places.primary_photo_path
```

But do not add this before the new places import is ready.

## Source Manifest Mapping

Use `photo_manifest.csv`.

Manifest columns:

```text
place_id
photo_source
photo_item_id
bundle_relative_file
bundle_relative_dir
source_url
original_file
bytes
content_type
width
height
place_name
review_id
review_rating
review_published_at
review_language
author_id
author_name
vibe_place_id
category
category_label
uploaded_by_owner
upload_date
photo_index
```

Mapping:

| Manifest column | `place_photos` column |
| --- | --- |
| `place_id` | `place_source_id` |
| constant `'google'` | `place_source` |
| `photo_source` | `photo_source` |
| `photo_item_id` | `photo_item_id` |
| generated path | `storage_path` |
| generated public URL | `public_url` |
| `source_url` | `source_url` |
| `original_file` | `original_file` |
| `bytes` | `bytes` |
| `content_type` | `content_type` |
| `width` | `width` |
| `height` | `height` |
| `review_id` | `review_id` |
| `review_rating` | `review_rating` |
| `review_published_at` | `review_published_at` |
| `review_language` | `review_language` |
| `author_id` | `author_id` |
| `author_name` | `author_name` |
| `vibe_place_id` | `vibe_place_id` |
| `category` | `category` |
| `category_label` | `category_label` |
| `uploaded_by_owner` | `uploaded_by_owner` |
| `upload_date` | `upload_date` |
| `photo_index` | `photo_index` |

## Implementation Plan

### 1. Supabase Setup

In Supabase dashboard:

1. Create Storage bucket:

   ```text
   place-photos
   ```

2. Make it public for MVP.
3. Keep real keys out of git.
4. Confirm backend env already has:

   ```text
   SUPABASE_URL
   SUPABASE_SERVICE_ROLE_KEY
   ```

5. Apply the `place_photos` migration through SQL Editor or Supabase CLI.

### 2. Migration

Add migration:

```text
supabase/migrations/008_create_place_photos.sql
```

This migration should create only photo metadata. It should not modify `places`.

### 3. Upload Script

Add script:

```text
scripts/photos/upload-place-photos.ts
```

The script should:

- read `photo_manifest.csv`;
- resolve `bundle_relative_file` relative to dataset root;
- generate deterministic Supabase Storage path;
- support `--dry-run`;
- support `--limit`;
- support `--offset` or cursor/resume;
- support `--concurrency`;
- skip existing objects when rerun;
- write a normalized metadata CSV or directly upsert rows into Supabase;
- print progress:

  ```text
  uploaded / skipped / failed / bytes uploaded
  ```

Recommended CLI shape:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --dry-run --limit 100
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --limit 100
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --concurrency 5
```

Do not start full upload until sample upload works.

### 4. Sample Upload

Run:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --dry-run --limit 100
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --limit 100
```

Verify:

- objects appear in Supabase Storage;
- public URLs open in browser;
- `place_photos` has 100 rows;
- no duplicate rows on rerun;
- sample includes both `review` and `vibe` if available.

### 5. Full Upload

After sample is clean:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --concurrency 5
```

If stable, increase carefully:

```bash
--concurrency 10
```

Do not use huge concurrency. We want predictable upload and resumability, not a
rate-limit party.

### 6. Future Places Import

When the new `locations.csv` includes coordinates:

- design the new `places` schema;
- wipe old test data;
- import new places with:

  ```text
  source='google'
  source_id=locations.place_id
  ```

- verify photo join:

  ```sql
  select count(*)
  from public.places p
  join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id;
  ```

### 7. Future API

Later, add photo fields to map/detail responses:

```json
{
  "primaryPhoto": {
    "id": 123,
    "url": "https://...",
    "width": 3000,
    "height": 4000,
    "source": "review"
  },
  "photoCount": 87
}
```

Map endpoint should return only a primary/thumbnail photo, not the full gallery.

Full gallery should be a separate place details endpoint later.

## Validation Queries

After metadata import:

```sql
select count(*) from public.place_photos;

select photo_source, count(*)
from public.place_photos
group by photo_source;

select count(distinct place_source_id)
from public.place_photos;

select place_source_id, count(*)
from public.place_photos
group by place_source_id
order by count(*) desc
limit 20;
```

After places import:

```sql
select count(*)
from public.place_photos ph
left join public.places p
  on p.source = ph.place_source
 and p.source_id = ph.place_source_id
where p.id is null;
```

Expected after final places import:

```text
0 unmatched photos
```

## Risks

- The current `locations.csv` has no latitude/longitude, so it cannot replace
  `places` yet.
- Google/Apify photo usage rights must be checked before public launch.
- 21 GB upload can take time and fail midway; script must be resumable.
- Public bucket is simplest for MVP, but private/signed URLs may be needed later
  if legal/product requirements change.
- Original images can be large; later we may need generated thumbnails/WebP
  variants for app performance.

## Test Plan

Code checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Script checks:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --dry-run --limit 100
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --limit 100
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --limit 100
```

Second non-dry run should skip already uploaded objects/metadata.

Manual checks:

- Open several public photo URLs.
- Check Storage folder layout.
- Check `place_photos` counts by source.
- Check no secrets are committed.

## Acceptance Criteria

- `place-photos` bucket exists.
- `place_photos` table exists.
- Upload script supports dry-run, limit, concurrency, and rerun safety.
- First 100 photos upload successfully.
- Metadata rows are created for uploaded photos.
- Rerunning the same sample does not create duplicates.
- Full upload can be started safely after sample validation.
