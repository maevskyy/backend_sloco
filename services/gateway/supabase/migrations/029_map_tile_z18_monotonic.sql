-- SLO-47: map_tile() — z>=18 gets the z17 cap (25) instead of the score floor,
-- so zooming from z17 to z18 only ever adds places.
--
-- DESTRUCTIVE: drops public.map_tile_min_score(integer). After this migration
-- nothing calls it (on prod 2026-10-03 only map_tile referenced it; the gateway
-- never did). No table, index or row is touched.
-- Undo: supabase/rollback/2026-10-03_029_rollback.sql (recreates the 014
-- function and the 017 map_tile body).
--
-- Why: 017 built tiles by two rules — z17 = top 25 per tile with no floor,
-- z>=18 = no cap but map_visibility_score >= 56. A sparse z17 tile keeps places
-- below 56 (the cap is not full); its z18 children drop them on the floor.
-- iOS requests z17/z18 tiles since maxzoom = 18 (2026-09-13), so markers vanished
-- on zoom-in: live 2026-09-29/10-03, tileVersion 3, 31 places lost around
-- Universitate, 10 in Old Town, 42 in Tbilisi centre — all with score < 56.
--
-- Why a cap of 25 is monotone: the candidate filter is the bare tile envelope
-- (the 64 in st_asmvtgeom only pads clipping), so a z18 tile is exactly one
-- quarter of its z17 parent, and every tile uses the same total order (id asc
-- breaks ties). The parent's top 25 put at most 25 places into any quarter, and
-- those are also that quarter's top by the same order, so a cap of 25 keeps them
-- all. A smaller cap at z>=18 would break this; "no cap" would let dense tiles
-- carry hundreds of features the client hides by collision anyway.
--
-- Three changes to the 017 body:
--   1. the z>=18 floor predicate is gone;
--   2. the limit for z>=17 is 25 (was 25 at z17, uncapped above);
--   3. map_visibility_score sorts NULLS LAST — the tile emits coalesce(score, 0),
--      so a NULL must not outrank every scored place (0 NULLs on 2026-10-03).
--
-- Rollout order: run this FIRST (psql -1 -f), then deploy with
-- MAP_TILE_VERSION=4 so Redis keys, the ETag and the client's ?v= roll together.
-- Deploying first would cache old tiles under v4 keys for 7 days.

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
    order by
      p.map_visibility_score desc nulls last,
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
        else 25
      end
    )
  )
  select coalesce(st_asmvt(tile_places.*, 'places', 4096, 'geom', 'id'), ''::bytea)
  from tile_places;
$$;

-- After the new body: a SQL function body does not record dependencies, so
-- dropping first would leave map_tile calling a missing function.
drop function if exists public.map_tile_min_score(integer);
