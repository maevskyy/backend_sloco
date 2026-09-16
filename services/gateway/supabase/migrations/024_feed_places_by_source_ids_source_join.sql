-- SLO-7: feed_places_by_source_ids() joins places on (source, source_id), not on
-- source_id alone.
--
-- Numbering: 017–023 were applied from the `dev` branch (2026-08-11 → 08-30) and
-- ported into this tree on 2026-09-16; 024 continues the sequence the database is at.
--
-- WARNING: DROPS AND RECREATES public.feed_places_by_source_ids — THE SIGNATURE
-- GAINS A DEFAULTED PARAMETER, SO CREATE OR REPLACE CANNOT BE USED. NO TABLE,
-- COLUMN OR ROW IS TOUCHED. The gateway calls it with the four named arguments
-- from migration 016; those keep working unchanged because the new parameter
-- defaults. Undo: supabase/rollback/2026-09-16_024_rollback.sql restores the
-- migration-016 body verbatim.
--
-- Why: the recommender returns source_ids, and the RPC hydrated them with
--   join public.places p on p.source_id = r.source_id
-- The only index that can serve this is places_source_source_id_key (source,
-- source_id). Without the leading column Postgres 17 has no skip scan, so every
-- one of the 200 ids walked the whole index: 113 000 shared buffers, 2.9 s cold /
-- 0.7 s warm on the 58k catalog (measured 2026-09-15, SLO-3 bench). Adding the
-- leading column makes each lookup a normal index probe: 1 500 buffers, 12 ms.
--
-- The catalog has a single source today ('sloco_ai'), so it is the default; the
-- gateway can start passing `source` explicitly once it carries it.

drop function if exists public.feed_places_by_source_ids(
  text[],
  double precision,
  double precision,
  integer
);

create or replace function public.feed_places_by_source_ids(
  source_ids text[],
  user_lat double precision default null,
  user_lng double precision default null,
  result_limit integer default 20,
  source text default 'sloco_ai'
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
    on p.source = feed_places_by_source_ids.source
   and p.source_id = r.source_id
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  order by r.input_rank asc, p.id asc
  limit least(greatest(coalesce(result_limit, 20), 1), 200);
$$;
