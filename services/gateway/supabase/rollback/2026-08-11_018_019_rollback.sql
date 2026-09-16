-- ROLLBACK for migrations 018 (search category/radius + norms) and 019 (feed
-- fallback category). Run this ONLY to undo them; it is not part of the
-- migration sequence.
--
-- What it restores: the exact function bodies from migrations 011 and 016, plus
-- the expression index 018 replaced. After running it, the pre-018 gateway code
-- works unchanged.
--
-- What it deliberately does NOT do: drop the columns 018 added
-- (name_norm / category_norm / primary_type_norm / types_norm / ai_tags_norm).
-- Dropping columns is destructive and pointless here — once the old trigger is
-- back they simply stop being updated and sit inert. If you ever really want
-- them gone, do it as its own reviewed change.
--
-- Safe to run twice.

-- 1. Remove the new function signatures ---------------------------------------

drop function if exists public.search_places(
  text, double precision, double precision, text, text, integer, text[], integer
);

drop function if exists public.feed_fallback_places(
  double precision, double precision, text, text, integer, text[]
);

-- 2. Restore the pre-018 trigger (search_keywords only) ------------------------

create or replace function public.set_place_search_keywords()
returns trigger
language plpgsql
set search_path = public, extensions
as $$
begin
  new.search_keywords := public.build_place_search_keywords(
    new.name,
    new.category,
    new.primary_type,
    new.types,
    new.ai_tags
  );

  return new;
end;
$$;

-- 3. Restore the pre-018 name index --------------------------------------------

create index if not exists places_name_trgm
  on public.places
  using gin (lower(public.f_unaccent(name)) gin_trgm_ops);

drop index if exists places_name_norm_trgm;

-- 4. Restore search_places as of migration 011 ---------------------------------

create or replace function public.search_places(
  q text,
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
  result_limit integer default 20
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
  q_norm text := lower(public.f_unaccent(trim(q)));
  city_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_city, '')))), '');
  country_norm text := nullif(lower(public.f_unaccent(trim(coalesce(user_country, '')))), '');
  safe_limit integer := least(greatest(coalesce(result_limit, 20), 1), 50);
  origin geography := null;
begin
  if q_norm is null or length(q_norm) < 2 then
    return;
  end if;

  perform set_config('pg_trgm.word_similarity_threshold', '0.3', true);

  if user_lat is not null and user_lng is not null then
    origin := st_setsrid(st_makepoint(user_lng, user_lat), 4326)::geography;
  end if;

  return query
  with candidates as (
    select
      p.*,
      lower(public.f_unaccent(p.name)) as name_norm_calc,
      lower(public.f_unaccent(coalesce(p.category, ''))) as category_norm_calc,
      lower(public.f_unaccent(coalesce(p.primary_type, ''))) as primary_type_norm_calc,
      lower(public.f_unaccent(coalesce(array_to_string(p.types, ' '), ''))) as types_norm_calc,
      lower(public.f_unaccent(coalesce(array_to_string(p.ai_tags, ' '), ''))) as ai_tags_norm_calc,
      case
        when origin is null then null
        else st_distance(p.geom::geography, origin)
      end as distance_m
    from public.places p
    where
      lower(public.f_unaccent(p.name)) %> q_norm
      or p.search_keywords %> q_norm
      or lower(public.f_unaccent(p.name)) like q_norm || '%'
  ),
  scored as (
    select
      c.*,
      word_similarity(q_norm, c.name_norm_calc) as name_match,
      word_similarity(q_norm, c.category_norm_calc) as category_match,
      greatest(
        word_similarity(q_norm, c.primary_type_norm_calc),
        word_similarity(q_norm, c.types_norm_calc)
      ) as type_match,
      word_similarity(q_norm, c.ai_tags_norm_calc) as tag_match,
      greatest(
        word_similarity(q_norm, c.name_norm_calc),
        0.6 * word_similarity(q_norm, c.search_keywords)
      ) as text_match,
      case when c.name_norm_calc = q_norm then 1.0 else 0.0 end as exact_name_boost,
      case when c.name_norm_calc like q_norm || '%' then 1.0 else 0.0 end as prefix_name_boost,
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
  text, double precision, double precision, text, text, integer
) to anon, authenticated, service_role;

-- 5. Restore feed_fallback_places as of migration 016 --------------------------

create or replace function public.feed_fallback_places(
  user_lat double precision default null,
  user_lng double precision default null,
  user_city text default null,
  user_country text default null,
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
