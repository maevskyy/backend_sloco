import { z } from "zod";

// Single source of truth for map request/response shapes.

const coordinateSchema = z.coerce.number().finite();

export const mapPlacesQuerySchema = z
  .object({
    swLat: coordinateSchema,
    swLng: coordinateSchema,
    neLat: coordinateSchema,
    neLng: coordinateSchema,
    limit: z.coerce.number().int().min(1).max(250).optional(),
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

export const mapPlaceSchema = z.object({
  id: z.number().int(),
  source: z.string(),
  sourceId: z.string(),
  name: z.string(),
  country: z.string(),
  city: z.string(),
  latitude: z.number(),
  longitude: z.number(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(1).max(4).nullable(),
  numberOfReviews: z.number().int().nullable(),
    rawCuisineStyle: z.string().nullable(),
    isSaved: z.boolean(),
  savedCollectionIds: z.array(z.string().uuid()),
  displayKind: z.enum(["featured", "dot"]),
  displayPriority: z.number().int().min(1)
});

export const mapPlacesResponseSchema = z.object({
  places: z.array(mapPlaceSchema)
});

export const mapSchemaRegistry = z.registry<{ id: string }>();

mapSchemaRegistry.add(mapPlacesQuerySchema, { id: "MapPlacesQuery" });
mapSchemaRegistry.add(mapPlaceSchema, { id: "MapPlace" });
mapSchemaRegistry.add(mapPlacesResponseSchema, { id: "MapPlacesResponse" });

export type MapPlacesQuery = z.infer<typeof mapPlacesQuerySchema>;
