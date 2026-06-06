create table if not exists public.saved_places (
  user_id uuid not null references auth.users(id) on delete cascade,
  place_id bigint not null references public.places(id) on delete cascade,
  created_at timestamptz not null default now(),
  last_viewed_at timestamptz,

  primary key (user_id, place_id)
);

alter table public.saved_places
  add column if not exists last_viewed_at timestamptz;

create index if not exists saved_places_user_created_at_idx
  on public.saved_places (user_id, created_at desc);

create index if not exists saved_places_place_id_idx
  on public.saved_places (place_id);

alter table public.saved_places enable row level security;

create table if not exists public.saved_collections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  color_hex text,
  is_default boolean not null default false,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (user_id, name)
);

create unique index if not exists saved_collections_one_default_per_user_idx
  on public.saved_collections (user_id)
  where is_default = true;

create index if not exists saved_collections_user_sort_idx
  on public.saved_collections (user_id, sort_order, created_at);

alter table public.saved_collections enable row level security;

create table if not exists public.saved_collection_places (
  collection_id uuid not null references public.saved_collections(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  place_id bigint not null references public.places(id) on delete cascade,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),

  primary key (collection_id, place_id)
);

create index if not exists saved_collection_places_user_place_idx
  on public.saved_collection_places (user_id, place_id);

create index if not exists saved_collection_places_collection_sort_idx
  on public.saved_collection_places (collection_id, sort_order, created_at);

alter table public.saved_collection_places enable row level security;
