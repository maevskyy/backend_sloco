-- STEP 2 of 2 — merge places_staging into public.places, in batches.
--
--     cd services/gateway
--     psql "$SUPABASE_DB_URL" -f supabase/02_merge_places.sql
--
-- Why batched: public.places carries a per-row trigger (set_place_search_keywords) and two
-- GIN trgm indexes (name, search_keywords). Upserting 58k rows in one statement runs past
-- Supabase's 120 s statement_timeout. Each batch commits on its own, so progress survives
-- and the script can simply be re-run — the upsert is idempotent.
--
-- Existing rows keep their id and are refreshed; new rows are inserted. Never written here:
-- id, geom (generated), created_at, search_keywords (trigger), the photo columns, and `raw`
-- (not imported, so existing values are preserved rather than nulled).

\set ON_ERROR_STOP on
\timing on

set statement_timeout = 0;

create or replace procedure public.merge_places_staged(batch_size integer default 1000)
language plpgsql
as $$
declare
  lo bigint := 0;
  hi bigint;
  moved integer;
  done integer := 0;
begin
  select max(batch_id) into hi from places_staging;
  while lo < hi loop
    insert into public.places (source, source_id, name, country, city, category, latitude, longitude, google_maps_uri, primary_type, types, google_rating, google_user_rating_count, apify_review_count, apify_rating_avg, rating_count_for_score, bayesian_rating, rating_score_0_100, popularity_score_0_100, rating_confidence_0_100, price_level, price_min_ron, price_max_ron, ai_card_summary, ai_place_type_summary, ai_vibe, ai_what_to_expect, ai_food_and_drinks, ai_price, ai_service, ai_the_move, ai_watch_out, ai_tags, ai_tags_json, ai_confidence, axis_quiet_lively, axis_work_social, axis_day_night, axis_casual_premium, axis_drinks_food, axis_local_tourist, axis_cheap_expensive, axis_traditional_experimental, map_visibility_score, map_visibility_rank, map_min_zoom_global, serves, features, attributes)
    select s.source, s.source_id, s.name, s.country, s.city, s.category, s.latitude, s.longitude, s.google_maps_uri, s.primary_type, s.types, s.google_rating, s.google_user_rating_count, s.apify_review_count, s.apify_rating_avg, s.rating_count_for_score, s.bayesian_rating, s.rating_score_0_100, s.popularity_score_0_100, s.rating_confidence_0_100, s.price_level, s.price_min_ron, s.price_max_ron, s.ai_card_summary, s.ai_place_type_summary, s.ai_vibe, s.ai_what_to_expect, s.ai_food_and_drinks, s.ai_price, s.ai_service, s.ai_the_move, s.ai_watch_out, s.ai_tags, s.ai_tags_json, s.ai_confidence, s.axis_quiet_lively, s.axis_work_social, s.axis_day_night, s.axis_casual_premium, s.axis_drinks_food, s.axis_local_tourist, s.axis_cheap_expensive, s.axis_traditional_experimental, s.map_visibility_score, s.map_visibility_rank, s.map_min_zoom_global, s.serves, s.features, s.attributes
    from places_staging s
    where s.batch_id > lo and s.batch_id <= lo + batch_size
    on conflict (source, source_id) do update set
    name = excluded.name,
    country = excluded.country,
    city = excluded.city,
    category = excluded.category,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    google_maps_uri = excluded.google_maps_uri,
    primary_type = excluded.primary_type,
    types = excluded.types,
    google_rating = excluded.google_rating,
    google_user_rating_count = excluded.google_user_rating_count,
    apify_review_count = excluded.apify_review_count,
    apify_rating_avg = excluded.apify_rating_avg,
    rating_count_for_score = excluded.rating_count_for_score,
    bayesian_rating = excluded.bayesian_rating,
    rating_score_0_100 = excluded.rating_score_0_100,
    popularity_score_0_100 = excluded.popularity_score_0_100,
    rating_confidence_0_100 = excluded.rating_confidence_0_100,
    price_level = excluded.price_level,
    price_min_ron = excluded.price_min_ron,
    price_max_ron = excluded.price_max_ron,
    ai_card_summary = excluded.ai_card_summary,
    ai_place_type_summary = excluded.ai_place_type_summary,
    ai_vibe = excluded.ai_vibe,
    ai_what_to_expect = excluded.ai_what_to_expect,
    ai_food_and_drinks = excluded.ai_food_and_drinks,
    ai_price = excluded.ai_price,
    ai_service = excluded.ai_service,
    ai_the_move = excluded.ai_the_move,
    ai_watch_out = excluded.ai_watch_out,
    ai_tags = excluded.ai_tags,
    ai_tags_json = excluded.ai_tags_json,
    ai_confidence = excluded.ai_confidence,
    axis_quiet_lively = excluded.axis_quiet_lively,
    axis_work_social = excluded.axis_work_social,
    axis_day_night = excluded.axis_day_night,
    axis_casual_premium = excluded.axis_casual_premium,
    axis_drinks_food = excluded.axis_drinks_food,
    axis_local_tourist = excluded.axis_local_tourist,
    axis_cheap_expensive = excluded.axis_cheap_expensive,
    axis_traditional_experimental = excluded.axis_traditional_experimental,
    map_visibility_score = excluded.map_visibility_score,
    map_visibility_rank = excluded.map_visibility_rank,
    map_min_zoom_global = excluded.map_min_zoom_global,
    serves = excluded.serves,
    features = excluded.features,
    attributes = excluded.attributes,
      updated_at = now();
    get diagnostics moved = row_count;
    done := done + moved;
    lo := lo + batch_size;
    commit;
    raise notice 'merged % / % rows', done, hi;
  end loop;
end;
$$;

call public.merge_places_staged(1000);

drop procedure public.merge_places_staged(integer);
drop table if exists places_staging;

-- verification
select city, country, count(*) as places
from public.places
where source = 'sloco_ai'
group by city, country
order by city;

select count(*) as total_sloco_ai from public.places where source = 'sloco_ai';
