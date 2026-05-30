-- TASKS 14: make the map endpoint bbox-only by dropping the city filter.
-- The bbox (with the GiST index on geom) already determines the visible area,
-- so the exact-match city filter is redundant and caused city/bbox mismatch
-- bugs. The function keeps the same return columns; only the city argument and
-- its WHERE clause are removed.

drop function if exists public.places_in_bbox(
  text,
  double precision,
  double precision,
  double precision,
  double precision,
  integer
);

create or replace function public.places_in_bbox(
  sw_lat double precision,
  sw_lng double precision,
  ne_lat double precision,
  ne_lng double precision,
  result_limit integer default 100
)
returns table (
  id bigint,
  source text,
  source_id text,
  name text,
  country text,
  city text,
  category text,
  latitude double precision,
  longitude double precision,
  rating numeric,
  price_level smallint,
  reviews_count integer,
  attributes jsonb
)
language sql
stable
as $$
  select
    p.id,
    p.source,
    p.source_id,
    p.name,
    p.country,
    p.city,
    p.category,
    p.latitude,
    p.longitude,
    p.rating,
    p.price_level,
    p.reviews_count,
    p.attributes
  from public.places p
  where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
  order by
    case when p.rating is null then 1 else 0 end,
    p.rating desc,
    p.id asc
  limit least(greatest(coalesce(result_limit, 100), 1), 200);
$$;
