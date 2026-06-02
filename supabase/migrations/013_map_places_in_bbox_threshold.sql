drop function if exists public.map_places_in_bbox(
  double precision,
  double precision,
  double precision,
  double precision,
  integer
);

create or replace function public.map_places_in_bbox(
  sw_lat double precision,
  sw_lng double precision,
  ne_lat double precision,
  ne_lng double precision,
  min_score numeric default 0,
  result_limit integer default 250
)
returns table (
  id bigint,
  source text,
  source_id text,
  name text,
  category text,
  primary_type text,
  latitude double precision,
  longitude double precision,
  rating numeric,
  price_level smallint,
  reviews_count integer,
  google_rating numeric,
  google_user_rating_count integer,
  rating_score_0_100 numeric,
  popularity_score_0_100 numeric,
  map_visibility_score numeric,
  map_visibility_rank integer,
  primary_photo_path text,
  primary_photo_url text,
  primary_photo_width integer,
  primary_photo_height integer,
  primary_photo_source text
)
language sql
stable
as $$
  select
    p.id,
    p.source,
    p.source_id,
    p.name,
    p.category,
    p.primary_type,
    p.latitude,
    p.longitude,
    p.rating,
    p.price_level,
    p.reviews_count,
    p.google_rating,
    p.google_user_rating_count,
    p.rating_score_0_100,
    p.popularity_score_0_100,
    p.map_visibility_score,
    p.map_visibility_rank,
    p.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source
  from public.places p
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
    and coalesce(p.map_visibility_score, 0) >= coalesce(min_score, 0)
  order by
    p.map_visibility_score desc,
    p.rating_score_0_100 desc nulls last,
    p.popularity_score_0_100 desc nulls last,
    p.google_rating desc nulls last,
    p.google_user_rating_count desc nulls last,
    p.id asc
  limit least(greatest(coalesce(result_limit, 250), 1), 1000);
$$;
