import { z } from "zod";
import { placeBucketsQuerySchema } from "../../places/index.js";

const coordinateSchema = z.coerce.number().finite();
const optionalContextSchema = z.string().trim().min(1).max(100).optional();

export const searchPlacesQuerySchema = z
  .object({
    q: z.string().trim().min(2).max(100).optional(),
    category: placeBucketsQuerySchema,
    radiusMeters: z.coerce.number().int().min(1).max(50_000).optional(),
    lat: coordinateSchema.optional(),
    lng: coordinateSchema.optional(),
    city: optionalContextSchema,
    country: optionalContextSchema,
    limit: z.coerce.number().int().min(1).max(50).default(20)
  })
  .refine((query) => (query.lat === undefined) === (query.lng === undefined), {
    message: "lat and lng must be sent together",
    path: ["lat"]
  })
  .refine((query) => query.q !== undefined || query.category !== undefined, {
    message: "either q or category is required",
    path: ["q"]
  });

export const searchPrimaryPhotoSchema = z.object({
  path: z.string(),
  url: z.string().nullable(),
  width: z.number().int().nullable(),
  height: z.number().int().nullable(),
  source: z.string().nullable()
});

export const searchPlaceResultSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  category: z.string(),
  primaryType: z.string().nullable(),
  city: z.string(),
  country: z.string(),
  formattedAddress: z.string().nullable(),
  latitude: z.number(),
  longitude: z.number(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(0).max(4).nullable(),
  primaryPhoto: searchPrimaryPhotoSchema.nullable(),
  distanceMeters: z.number().nullable(),
  matchReason: z.enum(["name", "category", "type", "tag"]),
  isSaved: z.boolean()
});

export const searchPlacesResponseSchema = z.object({
  query: z.string(),
  places: z.array(searchPlaceResultSchema)
});

export const searchSchemaRegistry = z.registry<{ id: string }>();

searchSchemaRegistry.add(searchPlacesQuerySchema, { id: "SearchPlacesQuery" });
searchSchemaRegistry.add(searchPrimaryPhotoSchema, { id: "SearchPrimaryPhoto" });
searchSchemaRegistry.add(searchPlaceResultSchema, { id: "SearchPlaceResult" });
searchSchemaRegistry.add(searchPlacesResponseSchema, {
  id: "SearchPlacesResponse"
});

export type SearchPlacesQuery = z.infer<typeof searchPlacesQuerySchema>;
