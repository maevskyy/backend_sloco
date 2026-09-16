-- SLO-5: feed_fallback_places() stops scanning and hydrating the whole catalog.
--
-- Numbering: 017–023 were applied from the `dev` branch and ported into this tree
-- on 2026-09-16; 024/025 continue the sequence the database is at.
--
-- Non-destructive: one new expression index + CREATE OR REPLACE of the function
-- body with the SAME six-argument signature migration 021 gave it. No table,
-- column or row is touched. Callers sending the five migration-016 arguments
-- keep working (category_keywords defaults). Undo:
-- supabase/rollback/2026-09-16_025_rollback.sql restores the 021 body verbatim.
--
-- Why (SLO-3 bench, 2026-09-16, before): Bucharest limit 200 = 28 s cold /
-- 3.3 s warm; Berlin + cafe bucket = 8 s / 5.6 s; the anonymous feed without a
-- city ran past PostgREST's 8 s statement_timeout and answered HTTP 500 five
-- times out of five. Three causes, all in the 021 body:
--   1. the city cut was `lower(f_unaccent(p.city)) = lower(f_unaccent(user_city))`
--      with no index on that expression → Seq Scan over 58 427 rows;
--   2. the planner estimated 292 rows for that filter (12 084 real) and joined
--      place_photos for EVERY candidate before sorting — 47k buffers of photos
--      to keep 200;
--   3. `select p.*` in the scored CTE dragged the 3 KB row (ai_* texts,
--      attributes) through the sort for all candidates.
--
-- Now:
--   * expression index places_city_norm_idx on lower(f_unaccent(city)) — the
--     cheapest "city_norm" until SLO-23 gives the hot table a real column;
--   * candidates are scored on a handful of narrow columns, sorted, cut to the
--     limit — and only THEN joined back to places and place_photos (≤ 200 rows);
--   * the city cut is written as two UNION ALL branches gated on whether a city
--     was sent, so the equality reaches the index even when the argument
--     arrives as a bind parameter (PostgREST) instead of a literal;
--   * st_distance is computed once per candidate, not twice.
-- Ranking is unchanged: same feed_score, same tie-breakers, same bucket match.
-- The bucket LIKE stays (SLO-5 step 3 / SLO-24 replace it) — after the city cut
-- it runs over 5–12k rows instead of 58k.

create index if not exists places_city_norm_idx
  on public.places (lower(public.f_unaccent(city)));

-- Expression indexes get their own statistics only after ANALYZE; without it the
-- planner keeps guessing ~300 rows per city (12 084 real) and picks nested loops.
analyze public.places;

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
  with candidates as (
    -- Branch 1: a city was sent → equality on the indexed expression.
    select
      p.id,
      p.map_visibility_score,
      p.rating_score_0_100,
      p.popularity_score_0_100,
      case
        when user_lat is null or user_lng is null then null
        else st_distance(
          p.geom::geography,
          st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography
        )
      end as distance_m,
      (
        user_country is not null
        and lower(public.f_unaccent(p.country)) = lower(public.f_unaccent(user_country))
      ) as same_country
    from public.places p
    where
      user_city is not null
      and btrim(user_city) <> ''
      and lower(public.f_unaccent(p.city)) = lower(public.f_unaccent(user_city))
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

    union all

    -- Branch 2: no city → whole catalog (planner skips this branch entirely when
    -- a city was sent: the gate below is a one-time filter).
    select
      p.id,
      p.map_visibility_score,
      p.rating_score_0_100,
      p.popularity_score_0_100,
      case
        when user_lat is null or user_lng is null then null
        else st_distance(
          p.geom::geography,
          st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography
        )
      end as distance_m,
      (
        user_country is not null
        and lower(public.f_unaccent(p.country)) = lower(public.f_unaccent(user_country))
      ) as same_country
    from public.places p
    where
      (user_city is null or btrim(user_city) = '')
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
  ),
  ranked as (
    select
      c.id,
      c.map_visibility_score,
      c.rating_score_0_100,
      c.popularity_score_0_100,
      c.distance_m,
      (
        coalesce(c.map_visibility_score, 0)
        + coalesce(c.rating_score_0_100, 0) * 0.20
        + coalesce(c.popularity_score_0_100, 0) * 0.15
        + case when c.same_country then 10 else 0 end
        + case
            when c.distance_m is null then 0
            else 25 / (1 + c.distance_m / 1000)
          end
      ) as feed_score
    from candidates c
    order by
      feed_score desc,
      c.map_visibility_score desc,
      c.rating_score_0_100 desc nulls last,
      c.popularity_score_0_100 desc nulls last,
      c.id asc
    limit least(greatest(coalesce(result_limit, 20), 1), 200)
  )
  -- Hydrate the winners only: ≤ 200 primary-key lookups + ≤ 200 photo lookups.
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
    r.distance_m,
    p.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source
  from ranked r
  join public.places p
    on p.id = r.id
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  order by
    r.feed_score desc,
    r.map_visibility_score desc,
    r.rating_score_0_100 desc nulls last,
    r.popularity_score_0_100 desc nulls last,
    r.id asc;
$$;
