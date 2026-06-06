create extension if not exists pg_trgm;
create extension if not exists unaccent;

create or replace function public.f_unaccent(value text)
returns text
language sql
immutable
parallel safe
strict
set search_path = public, extensions
as $$
  select unaccent(value);
$$;

alter table public.places
  add column if not exists search_keywords text;

create or replace function public.build_place_search_keywords(
  place_name text,
  place_category text,
  place_primary_type text,
  place_types text[],
  place_ai_tags text[]
)
returns text
language sql
stable
set search_path = public, extensions
as $$
  select lower(public.f_unaccent(
    coalesce(place_name, '') || ' ' ||
    coalesce(place_category, '') || ' ' ||
    coalesce(place_primary_type, '') || ' ' ||
    coalesce(array_to_string(place_types, ' '), '') || ' ' ||
    coalesce(array_to_string(place_ai_tags, ' '), '')
  ));
$$;

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

update public.places
set search_keywords = public.build_place_search_keywords(
  name,
  category,
  primary_type,
  types,
  ai_tags
)
where search_keywords is distinct from public.build_place_search_keywords(
  name,
  category,
  primary_type,
  types,
  ai_tags
);

drop trigger if exists places_set_search_keywords on public.places;

create trigger places_set_search_keywords
before insert or update of name, category, primary_type, types, ai_tags
on public.places
for each row
execute function public.set_place_search_keywords();

create index if not exists places_name_trgm
  on public.places
  using gin (lower(public.f_unaccent(name)) gin_trgm_ops);

create index if not exists places_search_keywords_trgm
  on public.places
  using gin (search_keywords gin_trgm_ops);

drop function if exists public.search_places(
  text,
  double precision,
  double precision,
  text,
  text,
  integer
);

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
      lower(public.f_unaccent(p.name)) as name_norm,
      lower(public.f_unaccent(coalesce(p.category, ''))) as category_norm,
      lower(public.f_unaccent(coalesce(p.primary_type, ''))) as primary_type_norm,
      lower(public.f_unaccent(coalesce(array_to_string(p.types, ' '), ''))) as types_norm,
      lower(public.f_unaccent(coalesce(array_to_string(p.ai_tags, ' '), ''))) as ai_tags_norm,
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
  integer
) to anon, authenticated, service_role;
