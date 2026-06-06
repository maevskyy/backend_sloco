# TASKS 3: Supabase DB Foundation

## Goal

Connect the backend to Supabase as managed Postgres using the Supabase JS
client for simple CRUD, while keeping SQL/RPC available later for geo and
vector-heavy queries.

## Scope

In scope:

- Add `@supabase/supabase-js`.
- Add server-side Supabase client.
- Add Supabase env validation.
- Add `GET /health/supabase`.
- Add the first staging table migration for TripAdvisor CSV data.
- Import `dumps/sample_with_coordinates.csv` manually through Supabase UI.

Out of scope:

- Drizzle or Prisma.
- PostGIS and pgvector setup.
- Final `places` domain schema.
- Automatic migration execution from app startup.

## Supabase Setup

Create/select a Supabase project and apply:

```text
supabase/migrations/001_create_raw_tripadvisor_restaurants.sql
```

Then import:

```text
dumps/sample_with_coordinates.csv
```

into:

```text
public.raw_tripadvisor_restaurants
```

Column mapping:

- `Unnamed: 0` -> `source_row_index`
- `Name` -> `name`
- `City` -> `city`
- `Cuisine Style` -> `raw_cuisine_style`
- `Ranking` -> `ranking`
- `Rating` -> `rating`
- `Price Range` -> `price_range`
- `Number of Reviews` -> `number_of_reviews`
- `Reviews` -> `raw_reviews`
- `URL_TA` -> `tripadvisor_url`
- `ID_TA` -> `tripadvisor_id`
- `embedding_text` -> `embedding_text`
- `latitude` -> `latitude`
- `longitude` -> `longitude`

## Environment

Local and production env:

```text
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

The service role key is backend-only. Never expose it to iOS or public clients.

## Verification

Local checks:

```bash
pnpm build
pnpm test
pnpm lint
```

Manual Supabase checks:

```sql
select count(*) from public.raw_tripadvisor_restaurants;
select city, count(*) from public.raw_tripadvisor_restaurants group by city;
select min(latitude), max(latitude), min(longitude), max(longitude)
from public.raw_tripadvisor_restaurants;
```

After deploy:

```bash
curl http://52.18.13.69/health
curl http://52.18.13.69/health/supabase
```
