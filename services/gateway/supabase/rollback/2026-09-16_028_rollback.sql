-- Undo for migration 028 (SLO-4 follow-up). Restores public.search_places to the
-- migration-027 body verbatim (no city resolution from coordinates; radius as
-- st_dwithin inside the candidate predicate).
--
-- Idempotent: CREATE OR REPLACE, same eight-argument signature and RETURNS
-- TABLE. No index or data is touched. After running it, searches that send
-- coordinates without a city are slow again (heap fetch of every row inside the
-- radius) — run only to undo 028.

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
