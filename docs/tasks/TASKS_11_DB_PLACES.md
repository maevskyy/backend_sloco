# TASKS 11: Unified `places` Table

## Goal

Replace the TripAdvisor-specific staging table with a single, source-agnostic
`places` table that can hold data from multiple providers (TripAdvisor,
OpenStreetMap, and future sources) for taste/lifestyle-based discovery.

This is the move predicted in `docs/architecture/REPO_STRUCTURE.md`: *"raw
staging tables are allowed during MVP, but final domain tables should be planned
separately."*

## Context

We now have two very different source shapes in `dumps/`:

- `raw_tripadvisor_restaurants_import.csv` — TripAdvisor (rating, reviews, price,
  cuisine; fake Berlin coordinates).
- `bucharest_cafes.csv` — OpenStreetMap (real coordinates, structured tags, but
  no rating/price/reviews).

See the analysis trail for the full comparison. The current
`raw_tripadvisor_restaurants` table is too TripAdvisor-shaped to host OSM data
without large data loss.

## Decision

- One unified `places` table: **typed core columns + JSONB tail** ("Mongo on
  Postgres"). Single serving table; the map API reads only this.
- **Drop** `raw_tripadvisor_restaurants`. All data flows through `places`. TA
  data is re-imported via the TripAdvisor mapper (TASKS 12).
- **PostGIS** `geometry(Point, 4326)` + **GiST** for geo queries; **GIN** on the
  JSONB tail.
- Text core fields use stable fallback values:
  - `country`, `city`, `category` → `'others'` when unknown.
- Optional numeric signals stay nullable:
  - `rating`, `price_level`, `reviews_count` → `NULL` when the source has no
    value.
  - `embedding_text` stays nullable because it is generated in a later task.

## Schema

New migration: `supabase/migrations/002_create_places.sql`.

```sql
create extension if not exists postgis;

create table public.places (
  id            bigserial primary key,

  -- identity (namespaced source ids: osm:node/123, tripadvisor:d5529357)
  source        text not null,                 -- 'osm' | 'tripadvisor'
  source_id     text not null,
  unique (source, source_id),

  -- core (API filters / sorts / renders on these)
  name          text not null,
  country       text not null default 'others',
  city          text not null default 'others',
  category      text not null default 'others',
  latitude      double precision not null,
  longitude     double precision not null,
  geom          geometry(Point, 4326)
                generated always as (
                  st_setsrid(st_makepoint(longitude, latitude), 4326)
                ) stored,

  -- optional source signals
  rating        numeric,
  price_level   smallint,                      -- normalized 1..4
  reviews_count integer,

  -- recommendations
  embedding_text text,                          -- nullable, generated later

  -- flexible tail
  attributes    jsonb not null default '{}',    -- source-specific (OSM tags, TA cuisine/ranking/urls)
  raw           jsonb,                           -- untouched original record

  -- provenance
  fetched_at    timestamptz,
  created_at    timestamptz not null default now(),

  constraint places_rating_range_chk
    check (rating is null or (rating >= 0 and rating <= 5)),
  constraint places_price_level_range_chk
    check (price_level is null or (price_level between 1 and 4)),
  constraint places_reviews_count_range_chk
    check (reviews_count is null or reviews_count >= 0)
);

create index places_geom_gist        on public.places using gist (geom);
create index places_attributes_gin   on public.places using gin  (attributes);
create index places_country_city_idx on public.places (country, city);
create index places_category_idx     on public.places (category);
create index places_rating_idx       on public.places (rating desc);

drop table if exists public.raw_tripadvisor_restaurants;
```

### Missing value reference

| Field | Type | Unknown value |
| --- | --- | --- |
| `country`, `city`, `category` | text | `'others'` |
| `rating` | numeric | `NULL` |
| `price_level` | smallint | `NULL` |
| `reviews_count` | integer | `NULL` |
| `embedding_text` | text | `NULL` (generated later) |

## Geo Queries

Replace the current 4x range filters on a `(latitude, longitude)` btree with a
real bbox using the GiST index. Map viewport query becomes:

```sql
where geom && st_makeenvelope(:swLng, :swLat, :neLng, :neLat, 4326)
```

Exposed to the service via a Supabase RPC (`places_in_bbox`). This is a proper
spatial query instead of four inequality scans.

RPC signature:

```sql
public.places_in_bbox(
  city_filter text,
  sw_lat double precision,
  sw_lng double precision,
  ne_lat double precision,
  ne_lng double precision,
  result_limit integer
)
```

## Code Impact

- `src/modules/map/map.service.ts`: query `places` instead of
  `raw_tripadvisor_restaurants`; bbox via PostGIS; map new columns.
- `MapPlacePin` already carries `source` + `sourceId`, so the API shape barely
  changes.
- `src/modules/map/map.openapi.ts`: add `country`, keep nullable signal fields,
  and expose normalized `priceLevel: number | null` instead of a formatted
  price string.

## Test Plan

- Migration applies cleanly on Supabase; `\d places` shows columns + indexes.
- Insert one row; `select` with `st_makeenvelope` bbox returns it.
- `GET /v1/map/places?...` returns places after a sample import.
- `pnpm build && pnpm test && pnpm lint` pass after the service change.

## Assumptions

- PostGIS is available on Supabase (it is, via the `postgis` extension).
- Data volume is small; manual re-import (via TASKS 12 mappers) is fine.
- `tripadvisor:` / `osm:` namespacing is the long-term identity scheme.

## Dependencies

- TASKS 12 (integration mappers) depends on the final column set defined here.

## Future Follow-Ups

- `embedding_text` generation step (uniform across sources).
- Cross-source dedup (same physical place from OSM + TripAdvisor) by geo
  proximity + name.
- Personalization / recommendation scoring on top of `places`.
