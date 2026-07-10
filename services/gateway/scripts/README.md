# Scripts

Offline scripts for operator workflows. These scripts are not part of the
runtime Fastify app.

## Integration Mappers

Mappers convert raw provider dumps into the canonical `public.places` CSV import
format.

Canonical output columns:

```text
source, source_id, name, country, city, category,
latitude, longitude, rating, price_level, reviews_count,
embedding_text, attributes, raw, fetched_at
```

`attributes` and `raw` are valid JSON strings for Supabase `jsonb` columns.
Missing optional numeric signals are emitted as empty CSV values, which import as
`NULL`.

## TripAdvisor

Input:

```text
dumps/raw_tripadvisor_restaurants_import.csv
```

Run:

```bash
pnpm map:tripadvisor dumps/raw_tripadvisor_restaurants_import.csv --out dumps/tripadvisor_places.csv
```

Output:

```text
dumps/tripadvisor_places.csv
```

## OpenStreetMap

Input:

```text
dumps/bucharest_cafes.csv
```

Run:

```bash
pnpm map:osm dumps/bucharest_cafes.csv --out dumps/osm_bucharest_places.csv
```

Output:

```text
dumps/osm_bucharest_places.csv
```

## Sloco AI Catalog

The data team's enriched catalog (numeric Google `cid` in `place_id`), mapped into
the v2 `public.places` shape with `source = "sloco_ai"`. This is the current
**primary** places source.

Input (from the data handoff):

```text
catalog/locations_combined_food_ttd.csv
```

Run:

```bash
pnpm map:sloco /path/to/handoff_for_backend/catalog/locations_combined_food_ttd.csv --out dumps/sloco_places.csv
```

Output:

```text
dumps/sloco_places.csv
```

Notes:

- `place_id` (numeric `cid`) maps to `source_id` — the join key the recommender
  and gateway share. It must match the embedding metadata's `place_id`.
- `ai_tags` / `types` are emitted as Postgres array literals (`{a,b}`);
  `ai_tags_json`, `serves`, `features`, `attributes`, `raw` as JSON strings.
- `ai_confidence` is mapped from the analyst's `high/medium/low` to numeric
  (`1` / `0.5` / `0`); already-numeric values pass through.
- Unmapped source columns (`theme`, `theme_group`, `ai_tags_csv`, `ai_model`, ...)
  are preserved under `attributes`; the full row is kept in `raw`.

Import via the same **Supabase Import** flow below (keep `id`, `geom`,
`created_at`, `updated_at`, and photo columns unmapped — the database owns them).

## Supabase Import

1. Run migration `supabase/migrations/002_create_places.sql`.
2. Open Supabase Table Editor.
3. Select `public.places`.
4. Import the generated `*_places.csv` file.
5. Map CSV columns by exact name.
6. Keep `id`, `geom`, and `created_at` unmapped because the database generates
   them.

## Place Photo Upload

Uploads the exported place photo dataset into Supabase Storage and upserts photo
metadata into `public.place_photos`.

Source dataset:

```text
/Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446
```

Before a real upload:

1. Apply `supabase/migrations/008_create_place_photos.sql`.
2. Make sure `.env` has:

   ```text
   SUPABASE_URL
   SUPABASE_SERVICE_ROLE_KEY
   ```

3. Run a dry run:

   ```bash
   pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --dry-run --limit 100
   ```

4. Run the first sample upload:

   ```bash
   pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --ensure-bucket --limit 100
   ```

Full upload, after sample validation:

```bash
pnpm photos:upload /Users/dimitriymaevskiy/Downloads/backend_dataset_20260531_214446 --ensure-bucket --concurrency 5
```

## Sloco Photo Index (R2)

Indexes the R2-hosted `cid`-keyed photo set into `public.place_photos`
(see `docs/tasks/TASKS_33_PHOTO_STORAGE.md`). Files are already in R2 — this
script only writes metadata rows; serving stays storage-agnostic via
`PHOTO_BASE_URL`.

1. Generate the manifest on the box that holds the photos:

   ```bash
   rclone lsf -R r2:sloco-photos/sloco_ai --files-only > sloco_photos_manifest.txt
   ```

2. Dry run, then index (needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` in `.env`):

   ```bash
   pnpm photos:index-sloco ~/Downloads/sloco_photos_manifest.txt \
     --base-url https://pub-<hash>.r2.dev --dry-run
   pnpm photos:index-sloco ~/Downloads/sloco_photos_manifest.txt \
     --base-url https://pub-<hash>.r2.dev
   ```

3. Backfill primary photos + counts (Supabase SQL Editor):

   ```sql
   update public.places p
      set primary_photo_path = pp.storage_path
     from (
       select distinct on (place_source, place_source_id)
              place_source, place_source_id, storage_path
         from public.place_photos
        where place_source = 'sloco_ai'
        order by place_source, place_source_id,
                 photo_index asc nulls last, photo_item_id asc
     ) pp
    where pp.place_source = p.source
      and pp.place_source_id = p.source_id;

   update public.places p
      set vibe_photo_count = c.cnt,
          total_photo_count = c.cnt
     from (
       select place_source_id, count(*) as cnt
         from public.place_photos
        where place_source = 'sloco_ai'
        group by place_source_id
     ) c
    where p.source = 'sloco_ai'
      and p.source_id = c.place_source_id;
   ```

When the CDN domain replaces the r2.dev URL, re-run the indexer with the new
`--base-url` (rows are upserted in place) — no file moves needed.
