-- TASKS 19: increase map candidate cap and align DB pre-sort with backend
-- quality ranking. The public function signature and return columns stay the
-- same; only candidate ordering and the internal cap change.

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
    (
      coalesce(p.rating, 0) * 10
      + least(log(greatest(coalesce(p.reviews_count, 0), 0) + 1) * 5, 20)
    ) desc,
    p.rating desc nulls last,
    p.reviews_count desc nulls last,
    p.id asc
  limit least(greatest(coalesce(result_limit, 100), 1), 1000);
$$;
