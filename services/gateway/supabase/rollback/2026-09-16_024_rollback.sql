-- Undo for migration 024 (SLO-7). Restores public.feed_places_by_source_ids to
-- the migration-016 shape: four parameters, join on source_id only. Body copied
-- verbatim from supabase/migrations/016_feed_rpc_pagination_window.sql.
--
-- Idempotent: safe to run twice. Drops the 5-argument function 024 created and
-- recreates the 4-argument one; no table, column or row is touched. After running
-- it the hydration RPC is slow again (full index walk per id) — run only to undo 024.

drop function if exists public.feed_places_by_source_ids(
  text[],
  double precision,
  double precision,
  integer,
  text
);

create or replace function public.feed_places_by_source_ids(
  source_ids text[],
  user_lat double precision default null,
  user_lng double precision default null,
  result_limit integer default 20
)
returns table (
  id bigint,
  source text,
  source_id text,
  name text,
  country text,
  city text,
  category text,
  primary_type text,
  latitude double precision,
  longitude double precision,
  rating numeric,
  price_level smallint,
  reviews_count integer,
  map_visibility_score numeric,
  ai_card_summary text,
  ai_place_type_summary text,
  ai_vibe text,
  ai_the_move text,
  ai_tags text[],
  formatted_address text,
  distance_m double precision,
  primary_photo_path text,
  primary_photo_url text,
  primary_photo_width integer,
  primary_photo_height integer,
  primary_photo_source text
)
language sql
stable
as $$
  with requested as (
    select
      value::text as source_id,
      ordinality::integer as input_rank
    from unnest(coalesce(source_ids, array[]::text[])) with ordinality as t(value, ordinality)
  )
  select
    p.id,
    p.source,
    p.source_id,
    p.name,
    p.country,
    p.city,
    p.category,
    p.primary_type,
    p.latitude,
    p.longitude,
    p.rating,
    p.price_level,
    p.reviews_count,
    p.map_visibility_score,
    p.ai_card_summary,
    p.ai_place_type_summary,
    p.ai_vibe,
    p.ai_the_move,
    p.ai_tags,
    p.formatted_address,
    case
      when user_lat is null or user_lng is null then null
      else st_distance(
        p.geom::geography,
        st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography
      )
    end as distance_m,
    p.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source
  from requested r
  join public.places p
    on p.source_id = r.source_id
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  order by r.input_rank asc, p.id asc
  limit least(greatest(coalesce(result_limit, 20), 1), 200);
$$;
