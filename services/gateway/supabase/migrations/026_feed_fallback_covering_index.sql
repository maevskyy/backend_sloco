-- SLO-5, step 3: the candidate scan of feed_fallback_places() becomes index-only.
--
-- Numbering: 017–023 were applied from the `dev` branch and ported into this tree
-- on 2026-09-16; 024–026 continue the sequence the database is at.
--
-- Non-destructive: one covering index, DROP of the single-column expression
-- index 025 created (superseded — same leading key), CREATE OR REPLACE of the
-- function body with the same six-argument signature. No table, column or row is
-- touched. Undo: supabase/rollback/2026-09-16_025_rollback.sql (restores the 021
-- body; the indexes stay, they are harmless).
--
-- Why: after 025 the Berlin + cafe-bucket feed still took 8.4 s cold. The city
-- cut used the index, but evaluating the bucket LIKE and reading the scoring
-- columns meant fetching all 37 813 Berlin heap rows — 3 KB each, ~10 700 cold
-- blocks at roughly 0.8 ms apiece on this instance. The narrow columns the
-- candidate stage needs fit in one 8 MB covering index:
--
--   key      lower(f_unaccent(city))        — the city cut
--   include  city                           — REQUIRED for index-only: without
--                                             the base column the planner
--                                             cannot recheck the expression
--                                             qual and falls back to heap reads
--            id, primary_type_norm, category_norm, country,
--            map_visibility_score, rating_score_0_100, popularity_score_0_100,
--            geom
--
-- Two body changes make the scan index-only:
--   * the bucket match is `LIKE ANY (array(...))` (a ScalarArrayOp with an
--     InitPlan) instead of `EXISTS (select … from unnest(…))` — a correlated
--     SubPlan filter blocks index-only scans;
--   * nothing else in the candidate branches references a column outside the
--     index. Semantics are identical: same word-boundary match on
--     primary_type_norm / category_norm, same score, same tie-breakers.
--
-- Measured in a rolled-back dry run (cold): Berlin + cafe 8.4 s → 1.05 s with
-- 5 515 heap fetches left over from pages the visibility map did not yet cover;
-- run `vacuum public.places` after this migration (outside the transaction) to
-- bring that to ~0. Bucharest + coords 261 ms; no city + coords 0.96 s.
-- This is still SLO-23's idea (a narrow hot projection) in index form — the
-- real places_hot replaces it later without a contract change.

create index if not exists places_feed_candidates_idx
  on public.places (lower(public.f_unaccent(city)))
  include (
    city,
    id,
    primary_type_norm,
    category_norm,
    country,
    map_visibility_score,
    rating_score_0_100,
    popularity_score_0_100,
    geom
  );

-- Same leading key as the covering index; keeping both would only cost writes.
drop index if exists public.places_city_norm_idx;

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
    -- Branch 1: a city was sent → equality on the indexed expression. Every
    -- column touched here lives in places_feed_candidates_idx, so this is an
    -- index-only scan: the 3 KB heap rows are not read until the final join.
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
        or (' ' || p.primary_type_norm || ' ') like any (array(select '% ' || kw || ' %' from unnest(category_keywords) kw))
        or (' ' || p.category_norm || ' ') like any (array(select '% ' || kw || ' %' from unnest(category_keywords) kw))
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
        or (' ' || p.primary_type_norm || ' ') like any (array(select '% ' || kw || ' %' from unnest(category_keywords) kw))
        or (' ' || p.category_norm || ' ') like any (array(select '% ' || kw || ' %' from unnest(category_keywords) kw))
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
