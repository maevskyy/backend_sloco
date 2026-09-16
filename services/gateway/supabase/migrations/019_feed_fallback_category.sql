-- TASKS_46: category filter on the feed fallback path (iOS ask
-- FEED_FILTERS_AND_DEPTH §2).
--
-- WARNING: THIS MIGRATION DROPS THE FUNCTION public.feed_fallback_places
-- (FIVE-ARGUMENT SIGNATURE) AND RECREATES IT WITH AN EXTRA DEFAULTED PARAMETER.
-- NO TABLE, COLUMN OR ROW IS TOUCHED. THE ANONYMOUS AND COLD-START FEED RUNS ON
-- THIS FUNCTION, SO THE FEED IS SERVED BY THE NEW BODY THE MOMENT IT COMMITS.
--
-- The new parameter DEFAULTS to NULL, so callers that send the old five named
-- arguments keep working unchanged. Undo: supabase/rollback/
-- 2026-08-11_018_019_rollback.sql restores the migration-016 body verbatim.
--
-- Depends on migration 018 (the *_norm columns and their backfill).
--
-- feed_fallback_places() gains category_keywords text[] (default null). The
-- bucket match is applied in the scored CTE — BEFORE ordering and the limit —
-- so a filtered feed is still a full snapshot of the best matching places, not
-- "whichever survived a post-hoc cut". Matching is word-boundary over the
-- normalized category/primary_type/types, identical to search_places (018).
--
-- The signature changes, so the old function is dropped rather than replaced
-- (see the WARNING above); the body is the 016 version plus the filter.

drop function if exists public.feed_fallback_places(
  double precision,
  double precision,
  text,
  text,
  integer
);

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
            when user_city is not null
             and lower(public.f_unaccent(p.city)) = lower(public.f_unaccent(user_city))
            then 20
            else 0
          end
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
      category_keywords is null
      or array_length(category_keywords, 1) is null
      or exists (
        select 1
        from unnest(category_keywords) kw
        -- word-boundary match ("bar" must not match "barbecue restaurant")
        where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
           or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
           or ' ' || p.types_norm || ' ' like '% ' || kw || ' %'
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
