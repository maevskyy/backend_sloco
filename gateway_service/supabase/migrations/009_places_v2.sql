create extension if not exists postgis;

truncate table
  public.saved_collection_places,
  public.saved_places,
  public.places
restart identity cascade;

alter table public.places
  add column if not exists formatted_address text,
  add column if not exists short_formatted_address text,
  add column if not exists business_status text,
  add column if not exists google_maps_uri text,
  add column if not exists phone text,
  add column if not exists international_phone text,
  add column if not exists website_url text,
  add column if not exists primary_type text,
  add column if not exists types text[] not null default '{}',
  add column if not exists google_rating numeric,
  add column if not exists google_user_rating_count integer,
  add column if not exists apify_review_count integer,
  add column if not exists apify_rating_avg numeric,
  add column if not exists rating_count_for_score integer,
  add column if not exists bayesian_rating numeric,
  add column if not exists rating_score_0_100 numeric,
  add column if not exists popularity_score_0_100 numeric,
  add column if not exists rating_confidence_0_100 numeric,
  add column if not exists price_min_ron numeric,
  add column if not exists price_max_ron numeric,
  add column if not exists ai_card_summary text,
  add column if not exists ai_place_type_summary text,
  add column if not exists ai_vibe text,
  add column if not exists ai_what_to_expect text,
  add column if not exists ai_food_and_drinks text,
  add column if not exists ai_price text,
  add column if not exists ai_service text,
  add column if not exists ai_the_move text,
  add column if not exists ai_watch_out text,
  add column if not exists ai_tags text[] not null default '{}',
  add column if not exists ai_tags_json jsonb not null default '[]',
  add column if not exists ai_confidence numeric,
  add column if not exists axis_quiet_lively smallint,
  add column if not exists axis_work_social smallint,
  add column if not exists axis_day_night smallint,
  add column if not exists axis_casual_premium smallint,
  add column if not exists axis_drinks_food smallint,
  add column if not exists axis_local_tourist smallint,
  add column if not exists axis_cheap_expensive smallint,
  add column if not exists axis_traditional_experimental smallint,
  add column if not exists map_visibility_score numeric not null default 0,
  add column if not exists map_visibility_rank integer,
  add column if not exists map_min_zoom_global smallint,
  add column if not exists review_photo_count integer not null default 0,
  add column if not exists vibe_photo_count integer not null default 0,
  add column if not exists total_photo_count integer not null default 0,
  add column if not exists primary_photo_path text,
  add column if not exists opening_hours jsonb,
  add column if not exists serves jsonb not null default '[]',
  add column if not exists features jsonb not null default '{}',
  add column if not exists google_details jsonb not null default '{}',
  add column if not exists apify_details jsonb not null default '{}',
  add column if not exists ai_details jsonb not null default '{}',
  add column if not exists photo_details jsonb not null default '{}',
  add column if not exists updated_at timestamptz not null default now();

alter table public.places
  alter column country set default 'romania',
  alter column city set default 'bucharest';

alter table public.places
  drop constraint if exists places_price_level_range_chk,
  drop constraint if exists places_google_rating_range_chk,
  drop constraint if exists places_apify_rating_avg_range_chk,
  drop constraint if exists places_non_negative_counts_chk,
  drop constraint if exists places_score_range_chk,
  drop constraint if exists places_axis_range_chk,
  drop constraint if exists places_map_min_zoom_global_chk;

alter table public.places
  add constraint places_price_level_range_chk
    check (price_level is null or (price_level between 0 and 4)),
  add constraint places_google_rating_range_chk
    check (google_rating is null or (google_rating >= 0 and google_rating <= 5)),
  add constraint places_apify_rating_avg_range_chk
    check (apify_rating_avg is null or (apify_rating_avg >= 0 and apify_rating_avg <= 5)),
  add constraint places_non_negative_counts_chk
    check (
      coalesce(reviews_count, 0) >= 0
      and coalesce(google_user_rating_count, 0) >= 0
      and coalesce(apify_review_count, 0) >= 0
      and coalesce(rating_count_for_score, 0) >= 0
      and review_photo_count >= 0
      and vibe_photo_count >= 0
      and total_photo_count >= 0
    ),
  add constraint places_score_range_chk
    check (
      (rating_score_0_100 is null or rating_score_0_100 between 0 and 100)
      and (
        popularity_score_0_100 is null
        or popularity_score_0_100 between 0 and 100
      )
      and (
        rating_confidence_0_100 is null
        or rating_confidence_0_100 between 0 and 100
      )
      and map_visibility_score >= 0
    ),
  add constraint places_axis_range_chk
    check (
      (axis_quiet_lively is null or axis_quiet_lively between 0 and 100)
      and (axis_work_social is null or axis_work_social between 0 and 100)
      and (axis_day_night is null or axis_day_night between 0 and 100)
      and (axis_casual_premium is null or axis_casual_premium between 0 and 100)
      and (axis_drinks_food is null or axis_drinks_food between 0 and 100)
      and (axis_local_tourist is null or axis_local_tourist between 0 and 100)
      and (axis_cheap_expensive is null or axis_cheap_expensive between 0 and 100)
      and (
        axis_traditional_experimental is null
        or axis_traditional_experimental between 0 and 100
      )
    ),
  add constraint places_map_min_zoom_global_chk
    check (map_min_zoom_global is null or map_min_zoom_global between 1 and 22);

create index if not exists places_primary_type_idx
  on public.places (primary_type);

create index if not exists places_map_visibility_idx
  on public.places (map_visibility_score desc, map_visibility_rank asc);

create index if not exists places_ai_tags_gin
  on public.places using gin (ai_tags);

drop function if exists public.places_in_bbox(
  double precision,
  double precision,
  double precision,
  double precision,
  integer
);

create or replace function public.places_in_bbox(
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
  primary_type text,
  types text[],
  latitude double precision,
  longitude double precision,
  formatted_address text,
  short_formatted_address text,
  business_status text,
  google_maps_uri text,
  phone text,
  international_phone text,
  website_url text,
  rating numeric,
  price_level smallint,
  reviews_count integer,
  google_rating numeric,
  google_user_rating_count integer,
  apify_review_count integer,
  apify_rating_avg numeric,
  rating_count_for_score integer,
  bayesian_rating numeric,
  rating_score_0_100 numeric,
  popularity_score_0_100 numeric,
  rating_confidence_0_100 numeric,
  price_min_ron numeric,
  price_max_ron numeric,
  map_visibility_score numeric,
  map_visibility_rank integer,
  map_min_zoom_global smallint,
  ai_card_summary text,
  ai_place_type_summary text,
  ai_vibe text,
  ai_what_to_expect text,
  ai_food_and_drinks text,
  ai_price text,
  ai_service text,
  ai_the_move text,
  ai_watch_out text,
  ai_tags text[],
  ai_tags_json jsonb,
  ai_confidence numeric,
  axis_quiet_lively smallint,
  axis_work_social smallint,
  axis_day_night smallint,
  axis_casual_premium smallint,
  axis_drinks_food smallint,
  axis_local_tourist smallint,
  axis_cheap_expensive smallint,
  axis_traditional_experimental smallint,
  review_photo_count integer,
  vibe_photo_count integer,
  primary_photo_path text,
  primary_photo_url text,
  primary_photo_width integer,
  primary_photo_height integer,
  primary_photo_source text,
  total_photo_count integer,
  opening_hours jsonb,
  serves jsonb,
  features jsonb,
  google_details jsonb,
  apify_details jsonb,
  ai_details jsonb,
  photo_details jsonb,
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
    p.primary_type,
    p.types,
    p.latitude,
    p.longitude,
    p.formatted_address,
    p.short_formatted_address,
    p.business_status,
    p.google_maps_uri,
    p.phone,
    p.international_phone,
    p.website_url,
    p.rating,
    p.price_level,
    p.reviews_count,
    p.google_rating,
    p.google_user_rating_count,
    p.apify_review_count,
    p.apify_rating_avg,
    p.rating_count_for_score,
    p.bayesian_rating,
    p.rating_score_0_100,
    p.popularity_score_0_100,
    p.rating_confidence_0_100,
    p.price_min_ron,
    p.price_max_ron,
    p.map_visibility_score,
    p.map_visibility_rank,
    p.map_min_zoom_global,
    p.ai_card_summary,
    p.ai_place_type_summary,
    p.ai_vibe,
    p.ai_what_to_expect,
    p.ai_food_and_drinks,
    p.ai_price,
    p.ai_service,
    p.ai_the_move,
    p.ai_watch_out,
    p.ai_tags,
    p.ai_tags_json,
    p.ai_confidence,
    p.axis_quiet_lively,
    p.axis_work_social,
    p.axis_day_night,
    p.axis_casual_premium,
    p.axis_drinks_food,
    p.axis_local_tourist,
    p.axis_cheap_expensive,
    p.axis_traditional_experimental,
    p.review_photo_count,
    p.vibe_photo_count,
    p.primary_photo_path,
    ph.public_url as primary_photo_url,
    ph.width as primary_photo_width,
    ph.height as primary_photo_height,
    ph.photo_source as primary_photo_source,
    p.total_photo_count,
    p.opening_hours,
    p.serves,
    p.features,
    p.google_details,
    p.apify_details,
    p.ai_details,
    p.photo_details,
    p.attributes
  from public.places p
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path
  where p.geom && st_makeenvelope(sw_lng, sw_lat, ne_lng, ne_lat, 4326)
  order by
    p.map_visibility_score desc,
    p.rating_score_0_100 desc nulls last,
    p.popularity_score_0_100 desc nulls last,
    p.google_rating desc nulls last,
    p.google_user_rating_count desc nulls last,
    p.id asc
  limit least(greatest(coalesce(result_limit, 100), 1), 1000);
$$;
