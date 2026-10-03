-- Undo for migration 029 (SLO-47). Recreates public.map_tile_min_score from
-- migration 014 verbatim, then restores public.map_tile to the migration-017
-- body verbatim (z>=18 uncapped with the score floor 56).
--
-- Idempotent: CREATE OR REPLACE, same signatures. No table, index or row is
-- touched. The function goes first because the 017 body calls it.
--
-- After running it, deploy with MAP_TILE_VERSION=5 — do NOT go back to 3: tiles
-- built by the 029 body between the migration and its deploy may sit in Redis
-- under v3 keys.

create or replace function public.map_tile_min_score(z integer)
returns numeric
language sql
immutable
as $$
  select case
    when z <= 10 then 92
    when z <= 12 then 86
    when z <= 14 then 76
    when z <= 16 then 66
    else 56
  end::numeric;
$$;

create or replace function public.map_tile(z integer, x integer, y integer)
returns bytea
language sql
stable
as $$
  with bounds as (
    select st_tileenvelope(z, x, y) as geom
  ),
  tile_places as (
    select
      st_asmvtgeom(p.geom_3857, b.geom, 4096, 64, true) as geom,
      p.id,
      p.name,
      p.category,
      p.primary_type as "primaryType",
      p.price_level as "priceLevel",
      coalesce(p.map_visibility_score, 0)::double precision as "mapVisibilityScore",
      p.primary_photo_path as "primaryPhotoPath"
    from public.places p
    cross join bounds b
    where
      p.geom_3857 && b.geom
      and (
        z < 18
        or coalesce(p.map_visibility_score, 0) >= public.map_tile_min_score(z)
      )
    order by
      p.map_visibility_score desc,
      p.rating_score_0_100 desc nulls last,
      p.popularity_score_0_100 desc nulls last,
      p.google_rating desc nulls last,
      p.google_user_rating_count desc nulls last,
      p.id asc
    limit (
      case
        when z <= 12 then 6
        when z <= 15 then 10
        when z = 16 then 15
        when z = 17 then 25
        else null
      end
    )
  )
  select coalesce(st_asmvt(tile_places.*, 'places', 4096, 'geom', 'id'), ''::bytea)
  from tile_places;
$$;
