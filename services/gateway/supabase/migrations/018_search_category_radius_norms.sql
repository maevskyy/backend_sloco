-- TASKS_45 (+ the query half of TASKS_48): search category/radius + precomputed
-- normalization.
--
-- WARNING: THIS MIGRATION CONTAINS HIGH-RISK OPERATIONS. IT DROPS THE FUNCTION
-- public.search_places (SIX-ARGUMENT SIGNATURE) AND THE INDEX places_name_trgm,
-- AND RUNS A FULL-TABLE UPDATE OVER public.places (~12.6k ROWS) TO BACKFILL THE
-- NEW COLUMNS. NO ROW DATA IS DELETED AND NO EXISTING COLUMN IS OVERWRITTEN —
-- THE UPDATE WRITES ONLY THE COLUMNS THIS MIGRATION ADDS — BUT THE FUNCTION AND
-- INDEX ARE REPLACED, NOT PRESERVED. RUN IT DURING A QUIET DEPLOY WINDOW.
--
-- The dropped function is recreated below with a superset signature whose new
-- parameters all DEFAULT to NULL, so callers that send the old six named
-- arguments keep working unchanged. Undo: supabase/rollback/
-- 2026-08-11_018_019_rollback.sql restores the migration-011 bodies verbatim.
--
-- Two changes in one rewrite of search_places():
--
-- 1. PERFORMANCE (TASKS_48): the old function recomputed
--    lower(f_unaccent(...)) over name/category/primary_type/types/ai_tags for
--    EVERY candidate row on EVERY query (measured live: 0.4-0.8 s of query time
--    on warm requests, worse on short/broad queries). Those normalizations are
--    now stored columns maintained by the existing search-keywords trigger, and
--    the function scores against them.
--
-- 2. CONTRACT (TASKS_45, iOS ask SEARCH_CATEGORY_FILTER): optional
--    category_keywords text[] (word-boundary matched against the normalized
--    category/primary_type/types) and radius_meters (a HARD st_dwithin cut, not
--    a ranking boost). q becomes optional: category-only requests are "browse
--    mode", ordered by distance when an origin is given, else by
--    map_visibility_score. The gateway validates that at least one of q /
--    category is present.
--
-- Schema footprint: adds five nullable columns, backfills them, swaps the name
-- trigram index from the expression form (places_name_trgm) to the stored-column
-- form (places_name_norm_trgm), and replaces two functions. See the WARNING at
-- the top for the destructive parts.

-- 1. Stored normalized columns -------------------------------------------------

alter table public.places
  add column if not exists name_norm text,
  add column if not exists category_norm text,
  add column if not exists primary_type_norm text,
  add column if not exists types_norm text,
  add column if not exists ai_tags_norm text;

create or replace function public.set_place_search_keywords()
returns trigger
language plpgsql
set search_path = public, extensions
as $$
begin
  new.name_norm := lower(public.f_unaccent(coalesce(new.name, '')));
  new.category_norm := lower(public.f_unaccent(coalesce(new.category, '')));
  new.primary_type_norm := lower(public.f_unaccent(coalesce(new.primary_type, '')));
  new.types_norm := lower(public.f_unaccent(coalesce(array_to_string(new.types, ' '), '')));
  new.ai_tags_norm := lower(public.f_unaccent(coalesce(array_to_string(new.ai_tags, ' '), '')));
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

-- Backfill existing rows (the trigger only fires on the listed source columns).
update public.places
set
  name_norm = lower(public.f_unaccent(coalesce(name, ''))),
  category_norm = lower(public.f_unaccent(coalesce(category, ''))),
  primary_type_norm = lower(public.f_unaccent(coalesce(primary_type, ''))),
  types_norm = lower(public.f_unaccent(coalesce(array_to_string(types, ' '), ''))),
  ai_tags_norm = lower(public.f_unaccent(coalesce(array_to_string(ai_tags, ' '), '')))
where name_norm is null;

create index if not exists places_name_norm_trgm
  on public.places
  using gin (name_norm gin_trgm_ops);

drop index if exists places_name_trgm;

-- 2. search_places v2 ----------------------------------------------------------
-- The signature changes (new defaulted params + q becomes nullable), so the old
-- function is dropped rather than replaced.

drop function if exists public.search_places(
  text,
  double precision,
  double precision,
  text,
  text,
  integer
);

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

  -- Nothing to search or browse by.
  if q_norm is null and not has_buckets then
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
      case
        when origin is null then null
        else st_distance(p.geom::geography, origin)
      end as distance_m
    from public.places p
    where
      (
        q_norm is null
        or p.name_norm %> q_norm
        or p.search_keywords %> q_norm
        or p.name_norm like q_norm || '%'
      )
      and (
        not has_buckets
        or exists (
          select 1
          from unnest(category_keywords) kw
          -- word-boundary match ("bar" must not match "barbecue restaurant")
          where ' ' || p.primary_type_norm || ' ' like '% ' || kw || ' %'
             or ' ' || p.category_norm || ' ' like '% ' || kw || ' %'
             or ' ' || p.types_norm || ' ' like '% ' || kw || ' %'
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
      coalesce(word_similarity(q_norm, c.name_norm), 0) as name_match,
      coalesce(word_similarity(q_norm, c.category_norm), 0) as category_match,
      greatest(
        coalesce(word_similarity(q_norm, c.primary_type_norm), 0),
        coalesce(word_similarity(q_norm, c.types_norm), 0)
      ) as type_match,
      coalesce(word_similarity(q_norm, c.ai_tags_norm), 0) as tag_match,
      greatest(
        coalesce(word_similarity(q_norm, c.name_norm), 0),
        0.6 * coalesce(word_similarity(q_norm, c.search_keywords), 0)
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
    case when q_norm is null then 'category' else r.resolved_match_reason end as match_reason
  from ranked r
  left join public.place_photos ph
    on ph.place_source = r.source
   and ph.place_source_id = r.source_id
   and ph.storage_path = r.primary_photo_path
  order by
    case when q_norm is not null then r.rank_score end desc nulls last,
    case when q_norm is null and origin is not null then r.distance_m end asc nulls last,
    case when q_norm is null and origin is null then r.map_visibility_score end desc nulls last,
    r.id asc
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
