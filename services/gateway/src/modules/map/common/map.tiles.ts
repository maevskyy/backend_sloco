import { z } from "zod";

export const MAP_TILE_CACHE_PREFIX = "tile";
export const MAP_TILE_CONTENT_TYPE = "application/vnd.mapbox-vector-tile";
export const MAP_TILE_MAX_AGE_SECONDS = 31_536_000;
export const MAP_TILE_MIN_ZOOM = 1;
export const MAP_TILE_MAX_ZOOM = 22;

const tileCoordinateSchema = z.coerce.number().int().min(0);

export const mapTileParamsSchema = z
  .object({
    z: z.coerce.number().int().min(MAP_TILE_MIN_ZOOM).max(MAP_TILE_MAX_ZOOM),
    x: tileCoordinateSchema,
    y: z
      .string()
      .regex(/^\d+(?:\.mvt)?$/)
      .transform((value) => Number(value.replace(".mvt", "")))
  })
  .refine((tile) => tile.x < getTileAxisSize(tile.z), {
    message: "x is outside the tile range for z",
    path: ["x"]
  })
  .refine((tile) => tile.y < getTileAxisSize(tile.z), {
    message: "y is outside the tile range for z",
    path: ["y"]
  });

export type MapTileParams = z.infer<typeof mapTileParamsSchema>;

export type MapTileResult =
  | {
      statusCode: 200;
      body: Buffer;
      etag: string;
      cacheControl: string;
    }
  | {
      statusCode: 204;
      body: null;
      etag: string;
      cacheControl: string;
    };

export type MapTileStoreContract = {
  getTile(params: MapTileParams): Promise<Buffer>;
};

export type MapTileService = (
  params: MapTileParams
) => Promise<MapTileResult>;

export function getMapTileUrlTemplate(version: number) {
  return `/v1/map/tiles/{z}/{x}/{y}.mvt?v=${version}`;
}

export function getMapTileCacheKey(
  version: number,
  params: MapTileParams
) {
  return `${MAP_TILE_CACHE_PREFIX}:v${version}:${params.z}/${params.x}/${params.y}`;
}

export function getMapTileEtag(version: number) {
  return `"v${version}"`;
}

export function getMapTileCacheControl() {
  return `public, max-age=${MAP_TILE_MAX_AGE_SECONDS}, immutable`;
}

function getTileAxisSize(z: number) {
  return 2 ** z;
}
