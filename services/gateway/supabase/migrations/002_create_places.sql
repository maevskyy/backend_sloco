create extension if not exists postgis;

create table if not exists public.places (
  id bigserial primary key,

  source text not null,
  source_id text not null,

  name text not null,
  country text not null default 'others',
  city text not null default 'others',
  category text not null default 'others',
  latitude double precision not null,
  longitude double precision not null,
  geom geometry(Point, 4326)
    generated always as (
      st_setsrid(st_makepoint(longitude, latitude), 4326)
    ) stored,

  rating numeric,
  price_level smallint,
  reviews_count integer,

  embedding_text text,

  attributes jsonb not null default '{}',
  raw jsonb,

  fetched_at timestamptz,
  created_at timestamptz not null default now(),

  unique (source, source_id),
  constraint places_rating_range_chk
    check (rating is null or (rating >= 0 and rating <= 5)),
  constraint places_price_level_range_chk
    check (price_level is null or (price_level between 1 and 4)),
  constraint places_reviews_count_range_chk
    check (reviews_count is null or reviews_count >= 0)
);

create index if not exists places_geom_gist
  on public.places using gist (geom);

create index if not exists places_attributes_gin
  on public.places using gin (attributes);

create index if not exists places_country_city_idx
  on public.places (country, city);

create index if not exists places_category_idx
  on public.places (category);

create index if not exists places_rating_idx
  on public.places (rating desc);

drop function if exists public.places_in_bbox(
  text,
  double precision,
  double precision,
  double precision,
  double precision,
  integer
);

create or replace function public.places_in_bbox(
  city_filter text,
  sw_lat double precision,
  sw_lng double precision,
  ne_lat double precision,
  ne_lng double precision,
  result_limit integer default 100
)
returns table (
  id bigint,
  source text,
  source_id text,
  name text,
  country text,
  city text,
  category text,
  latitude double precision,
  longitude double precision,
  rating numeric,
  price_level smallint,
  reviews_count integer,
  attributes jsonb
)
language sql
stable
as $$
  select
    p.id,
    p.source,
    p.source_id,
    p.name,
    p.country,
    p.city,
    p.category,
    p.latitude,
    p.longitude,
    p.rating,
    p.price_level,
    p.reviews_count,
    p.attributes
  from public.places p
  where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
    and (
      city_filter is null
      or btrim(city_filter) = ''
      or p.city = city_filter
    )
  order by
    case when p.rating is null then 1 else 0 end,
    p.rating desc,
    p.id asc
  limit least(greatest(coalesce(result_limit, 100), 1), 200);
$$;

drop table if exists public.raw_tripadvisor_restaurants;
