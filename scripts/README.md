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

## Supabase Import

1. Run migration `supabase/migrations/002_create_places.sql`.
2. Open Supabase Table Editor.
3. Select `public.places`.
4. Import the generated `*_places.csv` file.
5. Map CSV columns by exact name.
6. Keep `id`, `geom`, and `created_at` unmapped because the database generates
   them.
