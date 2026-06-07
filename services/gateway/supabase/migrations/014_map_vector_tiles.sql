-- TASKS_32: PostGIS vector tiles for the map.
--
-- WARNING: THIS MIGRATION ALTERs public.places BY ADDING A GENERATED geom_3857
-- COLUMN. ON A LARGE TABLE THIS CAN REWRITE DATA AND TAKE TIME. RUN IT DURING A
-- QUIET DEPLOY WINDOW.

alter table public.places
  add column if not exists geom_3857 geometry(Point, 3857)
  generated always as (
    st_transform(st_setsrid(st_makepoint(longitude, latitude), 4326), 3857)
  ) stored;

create index if not exists places_geom_3857_gist
  on public.places
  using gist (geom_3857);

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
      and coalesce(p.map_visibility_score, 0) >= public.map_tile_min_score(z)
    order by
      p.map_visibility_score desc,
      p.rating_score_0_100 desc nulls last,
      p.popularity_score_0_100 desc nulls last,
      p.google_rating desc nulls last,
      p.google_user_rating_count desc nulls last,
      p.id asc
  )
  select coalesce(st_asmvt(tile_places.*, 'places', 4096, 'geom', 'id'), ''::bytea)
  from tile_places;
$$;
