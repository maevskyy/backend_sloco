create table if not exists public.place_photos (
  id bigserial primary key,

  place_source text not null default 'google',
  place_source_id text not null,

  photo_source text not null,
  photo_item_id text not null,

  storage_bucket text not null default 'place-photos',
  storage_path text not null,
  public_url text,

  source_url text,
  original_file text,

  bytes integer,
  content_type text,
  width integer,
  height integer,

  review_id text,
  review_rating numeric,
  review_published_at timestamptz,
  review_language text,

  author_id text,
  author_name text,

  vibe_place_id text,
  category text,
  category_label text,
  uploaded_by_owner boolean,
  upload_date timestamptz,
  photo_index integer,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint place_photos_source_chk
    check (photo_source in ('review', 'vibe')),
  constraint place_photos_unique_source_photo
    unique (place_source, place_source_id, photo_source, photo_item_id),
  constraint place_photos_unique_storage_path
    unique (storage_bucket, storage_path)
);

create index if not exists place_photos_place_idx
  on public.place_photos (place_source, place_source_id);

create index if not exists place_photos_primary_lookup_idx
  on public.place_photos (
    place_source,
    place_source_id,
    photo_source,
    photo_index
  );

