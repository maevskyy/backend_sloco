-- Two fixes for the category browse mode shipped in 018/019, both found in
-- production acceptance on 2026-08-11.
--
-- (A) CORRECTNESS — match the venue KIND only.
--     The `nature` chip returned restaurants: "Animaletto House", types
--     ['restaurant','bar','garden']. The catalog's `types` array is an
--     ATTRIBUTE bag, not a taxonomy — `garden` means "has a terrace", `bar`
--     means "serves drinks" — so matching it made every restaurant with outdoor
--     seating a nature destination and put coffee shops in `bar`. `types` is
--     also underscored (`wine_bar`, `coffee_shop`), so multi-word keywords never
--     matched it anyway: it contributed false positives and almost no true ones.
--     `primary_type` (mirrored into `category` by the importer) is the
--     authoritative human-readable kind ("wine bar", "performing arts theater").
--     Matching it alone also makes the SQL identical to the TS twin
--     (`matchesBucketKeywords`), so the personalized and fallback feed paths
--     agree exactly.
--
--     Measured over the 12 578-place catalog (kind-only vs kind+types):
--     cafe 2812 (was 3122) · food 7442 (7743) · bar 1372 (1726) · culture 130 (130)
--     nature 11 (35 — the 24 dropped are garden-terrace restaurants)
--     shopping 2 (9) · leisure 567 (594).
--
-- (B) PERFORMANCE — browse mode gets its own query path.
--     Measured live: category-only requests took 2.9-4.3 s, the slowest
--     endpoint in the app. With no text query the function still ran the full
--     text-scoring pipeline (six word_similarity calls per candidate against a
--     NULL query), computed st_distance for every row, and filtered with
--     st_dwithin(geom::geography, ...) — a cast that cannot use the geometry
--     index, so every request scanned the whole table.
--     Browse mode now:
--       * skips the scoring CTEs entirely (nothing to score without a query);
--       * orders by the KNN operator `geom::geography <-> origin`, which the new
--         functional GiST index answers nearest-first, so a limit-20 request
--         stops after ~20 index rows instead of sorting the catalog;
--       * keeps st_dwithin for the hard radius cut, now index-supported.
--
-- Function bodies + two indexes; no schema change. The 018/019 rollback script
-- covers this migration too (it restores the pre-018 bodies wholesale).

-- Index support for the radius filter and the KNN ordering.
create index if not exists places_geog_gist
  on public.places
  using gist ((geom::geography));

-- Supports the bucket LIKE '% kw %' predicates (leading wildcard needs trigram).
create index if not exists places_primary_type_norm_trgm
  on public.places
  using gin (primary_type_norm gin_trgm_ops);

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
set search_path = public, extensions
as $$
declare
  q_norm text := nullif(lower(public.f_unaccent(trim(coalesce(q, '')))), '');
  city_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_city, '')))), '');
  country_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_country, '')))), '');
  safe_limit integer := least(greatest(coalesce(result_limit, 20), 1), 50);
  origin geography := null;
  has_buckets boolean :=
    category_keywords is not null and array_length(category_keywords, 1) > 0;
begin
  if q_norm is not null and length(q_norm) < 2 then
    q_norm := null;
  end if;

  if q_norm is null and not has_buckets then
    return;
  end if;

  if user_lat is not null and user_lng is not null then
    origin := st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography;
  end if;

  -- ---------------------------------------------------------------- browse ---
  -- Category only: no scoring, KNN order when an origin is given.
  if q_norm is null then
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
      case
        when origin is null then null
        else st_distance(p.geom::geography, origin)
      end,
      'category'::text
    from public.places p
    left join public.place_photos ph
      on ph.place_source = p.source
     and ph.place_source_id = p.source_id
     and ph.storage_path = p.primary_photo_path
    where
      exists (
        select 1
        from unnest(category_keywords) kw
        -- venue KIND only (see header A); word-boundary so "bar" cannot match
        -- "barbecue restaurant"
        where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
           or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
      )
      and (
        radius_meters is null
        or origin is null
        or st_dwithin(p.geom::geography, origin, radius_meters)
      )
    order by
      case when origin is null then 0 else 1 end,
      -- KNN: index-ordered nearest-first (places_geog_gist)
      p.geom::geography <-> coalesce(origin, p.geom::geography),
      p.map_visibility_score desc,
      p.id asc
    limit safe_limit;

    return;
  end if;

  -- ------------------------------------------------------------------ text ---
  perform set_config('pg_trgm.word_similarity_threshold', '0.3', true);

  return query
  with candidates as (
    select
      p.*,
      case
        when origin is null then null
        else st_distance(p.geom::geography, origin)
      end as distance_m
    from public.places p
    where
      (
        p.name_norm %> q_norm
        or p.search_keywords %> q_norm
        or p.name_norm like q_norm || '%'
      )
      and (
        not has_buckets
        or exists (
          select 1
          from unnest(category_keywords) kw
          where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
             or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
        )
      )
      and (
        radius_meters is null
        or origin is null
        or st_dwithin(p.geom::geography, origin, radius_meters)
      )
  ),
  scored as (
    select
      c.*,
      word_similarity(q_norm, c.name_norm) as name_match,
      word_similarity(q_norm, c.category_norm) as category_match,
      greatest(
        word_similarity(q_norm, c.primary_type_norm),
        word_similarity(q_norm, c.types_norm)
      ) as type_match,
      word_similarity(q_norm, c.ai_tags_norm) as tag_match,
      greatest(
        word_similarity(q_norm, c.name_norm),
        0.6 * word_similarity(q_norm, c.search_keywords)
      ) as text_match,
      case when c.name_norm = q_norm then 1.0 else 0.0 end as exact_name_boost,
      case when c.name_norm like q_norm || '%' then 1.0 else 0.0 end as prefix_name_boost,
      case
        when city_norm is not null
         and lower(public.f_unaccent(c.city)) = city_norm
        then 1.0
        else 0.0
      end as same_city_boost,
      case
        when country_norm is not null
         and lower(public.f_unaccent(c.country)) = country_norm
        then 1.0
        else 0.0
      end as same_country_boost,
      case
        when c.distance_m is null then 0.0
        else 1.0 / (1.0 + c.distance_m / 1000.0)
      end as nearby_boost,
      coalesce(c.map_visibility_score, 0) / 100.0 as quality_boost,
      coalesce(c.popularity_score_0_100, 0) / 100.0 as popularity_boost
    from candidates c
  ),
  ranked as (
    select
      s.*,
      (
        100.0 * s.text_match
        + 30.0 * s.exact_name_boost
        + 15.0 * s.prefix_name_boost
        + 12.0 * s.same_city_boost
        + 6.0 * s.same_country_boost
        + 10.0 * s.nearby_boost
        + 8.0 * s.quality_boost
        + 5.0 * s.popularity_boost
      ) as rank_score,
      case
        when s.name_match >= greatest(s.category_match, s.type_match, s.tag_match) then 'name'
        when s.category_match >= greatest(s.type_match, s.tag_match) then 'category'
        when s.type_match >= s.tag_match then 'type'
        else 'tag'
      end as resolved_match_reason
    from scored s
  )
  select
    r.id,
    r.name,
    r.category,
    r.primary_type,
    r.city,
    r.country,
    r.formatted_address,
    r.latitude,
    r.longitude,
    r.rating,
    r.price_level,
    r.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source,
    r.distance_m,
    r.resolved_match_reason as match_reason
  from ranked r
  left join public.place_photos ph
    on ph.place_source = r.source
   and ph.place_source_id = r.source_id
   and ph.storage_path = r.primary_photo_path
  order by r.rank_score desc, r.id asc
  limit safe_limit;
end;
$$;

grant execute on function public.search_places(
  text,
  double precision,
  double precision,
  text,
  text,
  integer,
  text[],
  integer
) to anon, authenticated, service_role;

-- Feed fallback: same venue-kind-only bucket match (correctness fix A).

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
        -- venue KIND only, matching search_places and the TS twin
        where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
           or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
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
