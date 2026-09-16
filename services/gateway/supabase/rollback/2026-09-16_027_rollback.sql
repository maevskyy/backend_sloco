-- Undo for migration 027 (SLO-4). Restores public.search_places to the body
-- that was live before it (dev branch, migration 023 era; dumped from prod on
-- 2026-09-16 with pg_get_functiondef), copied verbatim.
--
-- Idempotent: CREATE OR REPLACE, same eight-argument signature and RETURNS
-- TABLE. The covering index places_city_candidates_idx is left in place on
-- purpose: feed_fallback_places (026) needs it, and this body does not use it.
-- After running it, search is slow again (no city cut, search_keywords trigram
-- over the whole catalog, p.* before LIMIT) and a Bucharest search returns
-- same-named places from other cities — run only to undo 027.

CREATE OR REPLACE FUNCTION public.search_places(q text DEFAULT NULL::text, user_lat double precision DEFAULT NULL::double precision, user_lng double precision DEFAULT NULL::double precision, user_city text DEFAULT NULL::text, user_country text DEFAULT NULL::text, result_limit integer DEFAULT 20, category_keywords text[] DEFAULT NULL::text[], radius_meters integer DEFAULT NULL::integer)
 RETURNS TABLE(id bigint, name text, category text, primary_type text, city text, country text, formatted_address text, latitude double precision, longitude double precision, rating numeric, price_level smallint, primary_photo_path text, primary_photo_url text, primary_photo_width integer, primary_photo_height integer, primary_photo_source text, distance_m double precision, match_reason text)
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'public', 'extensions'
AS $$
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
