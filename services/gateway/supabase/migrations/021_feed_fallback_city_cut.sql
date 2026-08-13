-- TASKS_50: city= on GET /v1/feed/places is a hard cut, not a +20 boost
-- (iOS ask CITIES_LIST.md).
--
-- Same signature as migration 020 — CREATE OR REPLACE, no DROP. No table,
-- column or row is touched. Anonymous and cold-start feeds run on this
-- function, so the new body is live the moment it commits.
--
-- Undo: supabase/rollback/2026-08-13_021_feed_city_cut_rollback.sql restores
-- the migration-020 body verbatim.
--
-- user_city used to add +20 when unaccent(place.city) matched. That left
-- other-city cards in later pages (measured: Tbilisi offset=150 still had
-- 5 Bucharest). The same unaccent/case-insensitive match is now a WHERE,
-- so a filtered fallback snapshot is still 200 deep and city-pure.
-- Unknown / unmatched names (e.g. "Bucuresti") match nothing → empty page.
-- The +20 boost is gone: every remaining row already matches.
-- Country stays a boost. Seeds/clusters are unchanged — this RPC is the
-- fallback path only; the personalized path cuts in the gateway.

create or replace function public.feed_fallback_places(
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
  result_limit integer default 20,
  category_keywords text[] default null
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
  with scored as (
    select
      p.*,
      case
        when user_lat is null or user_lng is null then null
        else st_distance(
          p.geom::geography,
          st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography
        )
      end as distance_m,
      (
        coalesce(p.map_visibility_score, 0)
        + coalesce(p.rating_score_0_100, 0) * 0.20
        + coalesce(p.popularity_score_0_100, 0) * 0.15
        + case
            when user_country is not null
             and lower(public.f_unaccent(p.country)) = lower(public.f_unaccent(user_country))
            then 10
            else 0
          end
        + case
            when user_lat is null or user_lng is null then 0
            else 25 / (
              1 + st_distance(
                p.geom::geography,
                st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography
              ) / 1000
            )
          end
      ) as feed_score
    from public.places p
    where
      (
        user_city is null
        or btrim(user_city) = ''
        or lower(public.f_unaccent(p.city)) = lower(public.f_unaccent(user_city))
      )
      and (
        category_keywords is null
        or array_length(category_keywords, 1) is null
        or exists (
          select 1
          from unnest(category_keywords) kw
          where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
             or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
        )
      )
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
    p.distance_m,
    p.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source
  from scored p
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  order by
    p.feed_score desc,
    p.map_visibility_score desc,
    p.rating_score_0_100 desc nulls last,
    p.popularity_score_0_100 desc nulls last,
    p.id asc
  limit least(greatest(coalesce(result_limit, 20), 1), 200);
$$;
