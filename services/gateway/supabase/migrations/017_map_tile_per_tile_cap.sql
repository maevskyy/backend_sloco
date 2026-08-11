-- TASKS_41: per-tile place cap in map_tile().
--
-- iOS ask MAP_TILE_DENSITY: one z13 tile carried up to 235 places (Tbilisi) /
-- 119 (Bucharest) — a global per-zoom score floor cannot serve both cities, so
-- the floor is replaced by a per-tile top-N cap. Each map_tile() call builds
-- exactly one tile and the CTE is already ordered best-first, so the cap is a
-- plain LIMIT (Postgres treats LIMIT NULL as no limit).
--
-- Cap per zoom band: z<=12 -> 6, z13-15 -> 10, z16 -> 15, z17 -> 25,
-- z>=18 -> uncapped. The old min-score floor is kept ONLY for the uncapped
-- z>=18 band (56, via map_tile_min_score) so max zoom does not regress into
-- unbounded clutter; below that the cap subsumes it and sparse tiles keep
-- everything.
--
-- Non-destructive: function body only (CREATE OR REPLACE, same signature).
-- Rollout order: run this FIRST, then deploy with MAP_TILE_VERSION=2 (the
-- deploy workflow renders it into .env) so Redis keys, the ETag and the
-- client's ?v= all roll to the capped tiles at once.

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
