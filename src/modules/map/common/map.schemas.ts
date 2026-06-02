import { z } from "zod";
import { MAP_PINS_SAFETY_CAP } from "./map.ranking.js";

// Single source of truth for map request/response shapes.

const coordinateSchema = z.coerce.number().finite();

export const mapPlacesQuerySchema = z
  .object({
    swLat: coordinateSchema,
    swLng: coordinateSchema,
    neLat: coordinateSchema,
    neLng: coordinateSchema,
    limit: z.coerce.number().int().min(1).max(MAP_PINS_SAFETY_CAP).optional(),
    zoom: z.coerce.number().int().min(1).max(22).optional()
  })
  .refine((query) => query.swLat <= query.neLat, {
    message: "swLat must be less than or equal to neLat",
    path: ["swLat"]
  })
  .refine((query) => query.swLng <= query.neLng, {
    message: "swLng must be less than or equal to neLng",
    path: ["swLng"]
  });

export const mapPrimaryPhotoSchema = z.object({
  path: z.string(),
  url: z.string().nullable(),
  width: z.number().int().nullable(),
  height: z.number().int().nullable(),
  source: z.string().nullable()
});

export const mapPlacePinSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  category: z.string(),
  primaryType: z.string().nullable(),
  latitude: z.number(),
  longitude: z.number(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(0).max(4).nullable(),
  mapVisibilityScore: z.number(),
  primaryPhoto: mapPrimaryPhotoSchema.nullable(),
  isSaved: z.boolean(),
  displayPriority: z.number().int().min(1)
});

export const mapPlacesMetaSchema = z.object({
  returnedCount: z.number().int().min(0),
  limit: z.number().int().min(0),
  requestedLimit: z.number().int().min(1).nullable(),
  candidateLimit: z.number().int().min(0),
  capped: z.boolean(),
  effectiveZoom: z.number().int().min(1).max(22),
  minScore: z.number(),
  safetyCap: z.number().int().min(0),
  capHit: z.boolean(),
  queryBounds: z.object({
    swLat: z.number(),
    swLng: z.number(),
    neLat: z.number(),
    neLng: z.number()
  })
});

export const mapPlacesResponseSchema = z.object({
  places: z.array(mapPlacePinSchema),
  meta: mapPlacesMetaSchema
});

export const mapSchemaRegistry = z.registry<{ id: string }>();

mapSchemaRegistry.add(mapPlacesQuerySchema, { id: "MapPlacesQuery" });
mapSchemaRegistry.add(mapPrimaryPhotoSchema, { id: "MapPrimaryPhoto" });
mapSchemaRegistry.add(mapPlacePinSchema, { id: "MapPlacePin" });
mapSchemaRegistry.add(mapPlacesMetaSchema, { id: "MapPlacesMeta" });
mapSchemaRegistry.add(mapPlacesResponseSchema, { id: "MapPlacesResponse" });

export type MapPlacesQuery = z.infer<typeof mapPlacesQuerySchema>;
