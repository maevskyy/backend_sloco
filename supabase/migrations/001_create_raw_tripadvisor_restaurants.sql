create table if not exists public.raw_tripadvisor_restaurants (
  id bigserial primary key,

  source_row_index integer,
  tripadvisor_id text not null unique,
  tripadvisor_url text not null,

  name text not null,
  city text not null,

  raw_cuisine_style text,
  ranking numeric,
  rating numeric,
  price_range text,
  number_of_reviews integer,
  raw_reviews text,

  embedding_text text not null,

  latitude double precision not null,
  longitude double precision not null,

  import_batch_id text,
  created_at timestamptz not null default now()
);

create index if not exists raw_tripadvisor_restaurants_city_idx
  on public.raw_tripadvisor_restaurants (city);

create index if not exists raw_tripadvisor_restaurants_rating_idx
  on public.raw_tripadvisor_restaurants (rating);

create index if not exists raw_tripadvisor_restaurants_lat_lng_idx
  on public.raw_tripadvisor_restaurants (latitude, longitude);
