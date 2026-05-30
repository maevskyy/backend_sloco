# TASKS 12: Integration Mappers

## Goal

Offline ETL scripts that convert each provider's raw dump into the canonical
`places` import format, so data can be manually imported into Supabase.

Each integration answers one question: *"raw source file -> our format"*. The
operator then imports the produced CSV into `public.places` by hand.

## Context

We ingest from multiple providers with incompatible shapes (TripAdvisor,
OpenStreetMap). We do not want provider quirks leaking into the database or the
API. Mappers isolate each provider behind a shared, canonical record contract
(the `places` columns from TASKS 11).

## Decision

- Language: **TypeScript**, run with `tsx` (single toolchain with the backend).
- Location: top-level `scripts/` (offline ETL, kept out of `src/`, which is the
  runtime app).
- One mapper per integration, split by provider. Shared canonical contract.
- Output: a CSV in the canonical `places` column order. The operator imports it
  manually via Supabase.

## Structure

```text
scripts/
  integrations/
    _shared/
      place-record.ts      # canonical columns + fallback constants + CSV writer
    tripadvisor/
      map.ts
    osm/
      map.ts
  README.md                # how to run a mapper
```

Run example:

```bash
pnpm tsx scripts/integrations/osm/map.ts dumps/bucharest_cafes.csv \
  > dumps/osm_bucharest_places.csv
```

## Canonical Record ("our format")

Defined once in `_shared/place-record.ts`, mirrors the `places` columns minus
DB-generated ones (`id`, `geom`):

```text
source, source_id, name, country, city, category,
latitude, longitude, rating, price_level, reviews_count,
embedding_text, attributes, raw, fetched_at
```

Fallback constants live here too:

```text
UNKNOWN_TEXT = "others"
```

`attributes` and `raw` are serialized as JSON strings in the CSV (valid JSON,
double quotes), ready for a `jsonb` column.

## Mapping Rules

### TripAdvisor (`scripts/integrations/tripadvisor/map.ts`)

Input: `dumps/raw_tripadvisor_restaurants_import.csv`.

| Canonical | From |
| --- | --- |
| `source` | `"tripadvisor"` |
| `source_id` | `tripadvisor_id` |
| `name` | `name` |
| `country` | derived from `city` (Berlin -> Germany), else `others` |
| `city` | `city` |
| `category` | `"restaurant"` (TA dataset is restaurants), else `others` |
| `rating` | `rating`, else null |
| `price_level` | `price_range` string -> `1..4` (see table), else null |
| `reviews_count` | `number_of_reviews`, else null |
| `embedding_text` | `embedding_text` (passthrough) |
| `attributes` | `{ raw_cuisine_style, ranking, tripadvisor_url, raw_reviews }` |
| `raw` | full original row as JSON |
| `fetched_at` | null/unknown |

Price normalization:

| TripAdvisor `price_range` | `price_level` |
| --- | --- |
| `$` | 1 |
| `$$ - $$$` | 2 |
| `$$$` | 3 |
| `$$$$` | 4 |
| empty / other | null |

### OpenStreetMap (`scripts/integrations/osm/map.ts`)

Input: `dumps/bucharest_cafes.csv`.

| Canonical | From |
| --- | --- |
| `source` | `"osm"` |
| `source_id` | `place_id` (e.g. `osm:node/4712948976`) |
| `name` | `name` |
| `country` | `addr:country` tag, else dump context (Bucharest -> Romania), else `others` |
| `city` | `addr:city` tag, else dump context (`Bucharest`), else `others` |
| `category` | `primary_type` / `amenity` tag (`cafe` -> `"cafe"`), else `others` |
| `rating` | null (OSM has none) |
| `price_level` | null |
| `reviews_count` | null |
| `embedding_text` | null (generated later) |
| `attributes` | parsed `tags` dict (+ `osm_meta`) |
| `raw` | parsed `raw` dict |
| `fetched_at` | `fetched_at` column |

### OSM Python-dict parsing (the tricky bit)

`tags`, `osm_meta`, and `raw` are **Python dict literals** (single quotes,
`True/False/None`), not JSON. The mapper must normalize them to JSON before
writing. Risks: apostrophes inside values, unicode city names. Plan:

- a small `pyDictToJson` helper (careful quote/keyword normalization), or a
  tolerant parser;
- validate by asserting output row count == input row count and spot-checking a
  few rows.

## Dependencies

- Final `places` column set from **TASKS 11**.
- CSV parsing/writing: add `csv-parse` + `csv-stringify` as **devDependencies**
  (used only by scripts, not by the runtime app). Hand-rolling CSV is unsafe
  with quoted commas/newlines and embedded JSON.

## Docs To Update

- `scripts/README.md` (new) — how to run a mapper, output convention.
- `dumps/README.md` — document `bucharest_cafes.csv` and the generated
  `*_places.csv` import files.
- `docs/architecture/REPO_STRUCTURE.md` — add the top-level `scripts/` folder.
- `AGENTS.md` repo map — add `scripts/` (structure change).

## Test Plan

For each mapper:

- run on its dump; assert output row count == input row count;
- every required canonical text/core field is non-empty; optional numeric
  signals may be null;
- output CSV parses; `attributes` / `raw` are valid JSON;
- spot-check 3 rows by hand;
- import into `public.places`; `GET /v1/map/places` returns the new rows.

## Assumptions

- Manual import into Supabase is enough for MVP (no automated load pipeline).
- One mapper per provider; new providers add a new folder, shared contract
  untouched.

## Future Follow-Ups

- `embedding_text` generation as a shared post-map step.
- A thin loader that pushes the canonical CSV into Supabase (replacing manual
  import) once there are more sources.
- Cross-source dedup before/at import.
