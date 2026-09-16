-- SLO-4: search_places() cuts by city, selects candidates index-only, hydrates
-- after LIMIT.
--
-- Numbering: 017–023 live in the database (applied from the `dev` branch) and
-- are not yet in this tree; 024–027 continue the sequence the database is at.
--
-- Non-destructive: one covering index that supersedes places_feed_candidates_idx
-- (same leading key, superset of INCLUDE columns — feed_fallback_places from 026
-- keeps its index-only plan), DROP of the superseded index, CREATE OR REPLACE of
-- search_places with the same eight-argument signature and RETURNS TABLE. No
-- table, column or row is touched. Undo: supabase/rollback/2026-09-16_027_rollback.sql
-- (restores the dev-branch body; the index stays, feed still needs it).
--
-- Why (SLO-3 bench, 2026-09-16, before): search_places('cafe', Bucharest)
-- 17.5 s cold / 8.7 s warm, 84k buffers, sort spilled to disk. Four causes:
--
--   1. No city cut. user_city was a +12 rank boost, so a Bucharest search
--      scanned Berlin and Tbilisi too — and returned them: places named exactly
--      "Cafe" in Tbilisi outranked Bucharest cafés (exact-name +30 beats
--      same-city +12). Now the city is a hard filter through the index. No
--      city (Anywhere) still searches the whole catalog.
--   2. `search_keywords %> q` as a candidate arm. The keyword bag is
--      name+category+primary_type+types+ai_tags; at word_similarity 0.3 the
--      word "cafe" matched 31 781 of 58 427 rows (restaurants, churches,
--      casinos). Category match is now a word-boundary LIKE on
--      primary_type_norm — the same rule the pill buckets use (020/026).
--   3. Operator direction. `column %> constant` runs ~50x slower than
--      `constant <% column` / word_similarity(constant, column) on this
--      instance (2.8 s vs 60 ms over 12k rows): pg_trgm caches the trigrams
--      of the LEFT argument, and with the constant on the right the cache
--      missed on every row. Both text arms are now written constant-first.
--   4. `p.*` for every candidate (TOAST ai_*, attributes) before sort/LIMIT.
--      Scoring reads only index columns; places + place_photos are joined for
--      the final <= 50 rows.
--
-- The covering index gets name_norm so the candidate stage (city cut + name
-- similarity + type word match + radius) is one index-only scan, bounded by the
-- size of the city slice, no heap reads before LIMIT:
--
--   key      lower(f_unaccent(city))
--   include  city (REQUIRED for index-only on an expression key), id, name_norm,
--            primary_type_norm, category_norm, country, map_visibility_score,
--            rating_score_0_100, popularity_score_0_100, geom
--
-- Ranking formula is unchanged (100·text + 30·exact + 15·prefix + 12·city +
-- 6·country + 10·nearby + 8·quality + 5·popularity) except text_match no
-- longer has the 0.6·keywords term: it is greatest(name, 0.6·primary_type),
-- which gives the same 60 points to a category hit. match_reason 'tag' can no
-- longer occur (ai_tags are not a match source any more); the other three
-- values are computed as before. Verified against the live function on
-- cafe/pizza/starbucks/sushi @ Bucharest: identical order once the out-of-city
-- rows are removed.
--
-- Prototype on prod (warm, no parallel): cafe @ Bucharest 110–130 ms with heap
-- access — the index-only version should be at or under that; browse cafe
-- bucket @ Bucharest 30 ms. Berlin (37.8k rows) is ~2.5x Bucharest.

create index if not exists places_city_candidates_idx
  on public.places (lower(public.f_unaccent(city)))
  include (
    city,
    id,
    name_norm,
    primary_type_norm,
    category_norm,
    country,
    map_visibility_score,
    rating_score_0_100,
    popularity_score_0_100,
    geom
  );

-- Superseded: same leading key, the new index covers everything 026 reads.
drop index if exists public.places_feed_candidates_idx;

analyze public.places;

create or replace function public.search_places(
  q text default null,
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
  result_limit integer default 20,
  category_keywords text[] default null,
  radius_meters integer default null
)
returns table (
  id bigint,
  name text,
  category text,
  primary_type text,
  city text,
  country text,
  formatted_address text,
  latitude double precision,
  longitude double precision,
  rating numeric,
  price_level smallint,
  primary_photo_path text,
  primary_photo_url text,
  primary_photo_width integer,
  primary_photo_height integer,
  primary_photo_source text,
  distance_m double precision,
  match_reason text
)
language plpgsql
stable
set search_path to 'public', 'extensions'
-- Parallel workers take ~300 ms to start on this instance and the scan is a
-- few MB of index. The planner reaches for them only because
-- st_distance(geography) is declared expensive (procost 10000).
set max_parallel_workers_per_gather = 0
as $$
declare
  q_norm text := nullif(lower(public.f_unaccent(trim(coalesce(q, '')))), '');
  city_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_city, '')))), '');
  country_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_country, '')))), '');
  safe_limit integer := least(greatest(coalesce(result_limit, 20), 1), 50);
  origin geography := null;
  -- '% kw %' patterns for the bucket match; null when no buckets were sent.
  -- A plain array parameter keeps the predicate a ScalarArrayOp, which an
  -- index-only scan can evaluate (a correlated EXISTS/unnest cannot).
  bucket_patterns text[] := null;
begin
  if q_norm is not null and length(q_norm) < 2 then
    q_norm := null;
  end if;

  if category_keywords is not null and array_length(category_keywords, 1) > 0 then
    bucket_patterns := array(
      select '% ' || kw || ' %' from unnest(category_keywords) kw
    );
  end if;

  if q_norm is null and bucket_patterns is null then
    return;
  end if;

  if user_lat is not null and user_lng is not null then
    origin := st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography;
  end if;

  -- ---------------------------------------------------------------- browse ---
  -- Category only: no text scoring. Nearest-first when an origin is given,
  -- otherwise by map_visibility_score.
  if q_norm is null then
    if city_norm is not null then
      -- City sent: index-only over the city slice, sort, then hydrate the
      -- <= 50 winners. Bounded by the city size whatever the origin is (a
      -- KNN walk from a Berlin origin towards Bucharest rows would visit all
      -- of Berlin first).
      return query
      with candidates as (
        select
          p.id,
          p.map_visibility_score,
          case
            when origin is null then null
            else st_distance(p.geom::geography, origin)
          end as distance_m
        from public.places p
        where lower(public.f_unaccent(p.city)) = city_norm
          and (
            (' ' || p.primary_type_norm || ' ') like any (bucket_patterns)
            or (' ' || p.category_norm || ' ') like any (bucket_patterns)
          )
          and (
            radius_meters is null
            or origin is null
            or st_dwithin(p.geom::geography, origin, radius_meters)
          )
        order by 3 asc nulls last, p.map_visibility_score desc, p.id asc
        limit safe_limit
      )
      select
        p.id,
        p.name,
        p.category,
        p.primary_type,
        p.city,
        p.country,
        p.formatted_address,
        p.latitude,
        p.longitude,
        p.rating,
        p.price_level,
        p.primary_photo_path,
        ph.public_url,
        ph.width,
        ph.height,
        ph.photo_source,
        c.distance_m,
        'category'::text
      from candidates c
      join public.places p on p.id = c.id
      left join public.place_photos ph
        on ph.place_source = p.source
       and ph.place_source_id = p.source_id
       and ph.storage_path = p.primary_photo_path
      order by c.distance_m asc nulls last, c.map_visibility_score desc, c.id asc;

      return;
    end if;

    if origin is not null then
      -- No city, origin given: KNN through places_geog_gist, stops after
      -- safe_limit matches. Same query as before this migration.
      return query
      select
        p.id,
        p.name,
        p.category,
        p.primary_type,
        p.city,
        p.country,
        p.formatted_address,
        p.latitude,
        p.longitude,
        p.rating,
        p.price_level,
        p.primary_photo_path,
        ph.public_url,
        ph.width,
        ph.height,
        ph.photo_source,
        st_distance(p.geom::geography, origin),
        'category'::text
      from public.places p
      left join public.place_photos ph
        on ph.place_source = p.source
       and ph.place_source_id = p.source_id
       and ph.storage_path = p.primary_photo_path
      where
        (
          (' ' || p.primary_type_norm || ' ') like any (bucket_patterns)
          or (' ' || p.category_norm || ' ') like any (bucket_patterns)
        )
        and (
          radius_meters is null
          or st_dwithin(p.geom::geography, origin, radius_meters)
        )
      order by
        p.geom::geography <-> origin,
        p.map_visibility_score desc,
        p.id asc
      limit safe_limit;

      return;
    end if;

    -- No city, no origin: whole catalog by quality, index-only then hydrate.
    return query
    with candidates as (
      select p.id, p.map_visibility_score
      from public.places p
      where
        (' ' || p.primary_type_norm || ' ') like any (bucket_patterns)
        or (' ' || p.category_norm || ' ') like any (bucket_patterns)
      order by p.map_visibility_score desc, p.id asc
      limit safe_limit
    )
    select
      p.id,
      p.name,
      p.category,
      p.primary_type,
      p.city,
      p.country,
      p.formatted_address,
      p.latitude,
      p.longitude,
      p.rating,
      p.price_level,
      p.primary_photo_path,
      ph.public_url,
      ph.width,
      ph.height,
      ph.photo_source,
      null::double precision,
      'category'::text
    from candidates c
    join public.places p on p.id = c.id
    left join public.place_photos ph
      on ph.place_source = p.source
     and ph.place_source_id = p.source_id
     and ph.storage_path = p.primary_photo_path
    order by c.map_visibility_score desc, c.id asc;

    return;
  end if;

  -- ------------------------------------------------------------------ text ---
  -- Candidates come from one index-only scan over the city slice (or the whole
  -- index when no city was sent — two UNION ALL branches, the planner drops
  -- the inactive one with a one-time filter). A candidate matches when the
  -- query is a word of its primary type or word-similar (>= 0.3) to its name;
  -- the cheap LIKE is evaluated first. Scoring uses index columns only; the
  -- <= 50 winners are then joined to places and place_photos.
  return query
  with candidates as (
    select
      p.id,
      p.name_norm,
      p.primary_type_norm,
      p.category_norm,
      p.country,
      p.geom,
      p.map_visibility_score,
      p.popularity_score_0_100,
      1.0::double precision as same_city_boost
    from public.places p
    where city_norm is not null
      and lower(public.f_unaccent(p.city)) = city_norm
      and (
        (' ' || p.primary_type_norm || ' ') like ('% ' || q_norm || ' %')
        or word_similarity(q_norm, p.name_norm) >= 0.3
      )
      and (
        bucket_patterns is null
        or (' ' || p.primary_type_norm || ' ') like any (bucket_patterns)
        or (' ' || p.category_norm || ' ') like any (bucket_patterns)
      )
      and (
        radius_meters is null
        or origin is null
        or st_dwithin(p.geom::geography, origin, radius_meters)
      )
    union all
    select
      p.id,
      p.name_norm,
      p.primary_type_norm,
      p.category_norm,
      p.country,
      p.geom,
      p.map_visibility_score,
      p.popularity_score_0_100,
      0.0::double precision
    from public.places p
    where city_norm is null
      and (
        (' ' || p.primary_type_norm || ' ') like ('% ' || q_norm || ' %')
        or word_similarity(q_norm, p.name_norm) >= 0.3
      )
      and (
        bucket_patterns is null
        or (' ' || p.primary_type_norm || ' ') like any (bucket_patterns)
        or (' ' || p.category_norm || ' ') like any (bucket_patterns)
      )
      and (
        radius_meters is null
        or origin is null
        or st_dwithin(p.geom::geography, origin, radius_meters)
      )
  ),
  scored as (
    select
      c.id,
      c.same_city_boost,
      word_similarity(q_norm, c.name_norm) as name_match,
      word_similarity(q_norm, c.category_norm) as category_match,
      word_similarity(q_norm, c.primary_type_norm) as type_match,
      case when c.name_norm = q_norm then 1.0 else 0.0 end as exact_name_boost,
      case when c.name_norm like q_norm || '%' then 1.0 else 0.0 end as prefix_name_boost,
      case
        when country_norm is not null
         and lower(public.f_unaccent(c.country)) = country_norm
        then 1.0
        else 0.0
      end as same_country_boost,
      case
        when origin is null then null
        else st_distance(c.geom::geography, origin)
      end as distance_m,
      coalesce(c.map_visibility_score, 0) / 100.0 as quality_boost,
      coalesce(c.popularity_score_0_100, 0) / 100.0 as popularity_boost
    from candidates c
  ),
  ranked as (
    select
      s.id,
      s.distance_m,
      (
        100.0 * greatest(s.name_match, 0.6 * s.type_match)
        + 30.0 * s.exact_name_boost
        + 15.0 * s.prefix_name_boost
        + 12.0 * s.same_city_boost
        + 6.0 * s.same_country_boost
        + 10.0 * case
            when s.distance_m is null then 0.0
            else 1.0 / (1.0 + s.distance_m / 1000.0)
          end
        + 8.0 * s.quality_boost
        + 5.0 * s.popularity_boost
      ) as rank_score,
      case
        when s.name_match >= greatest(s.category_match, s.type_match) then 'name'
        when s.category_match >= s.type_match then 'category'
        else 'type'
      end as resolved_match_reason
    from scored s
    order by 3 desc, s.id asc
    limit safe_limit
  )
  select
    p.id,
    p.name,
    p.category,
    p.primary_type,
    p.city,
    p.country,
    p.formatted_address,
    p.latitude,
    p.longitude,
    p.rating,
    p.price_level,
    p.primary_photo_path,
    ph.public_url,
    ph.width,
    ph.height,
    ph.photo_source,
    r.distance_m,
    r.resolved_match_reason
  from ranked r
  join public.places p on p.id = r.id
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  order by r.rank_score desc, r.id asc;
end;
$$;
