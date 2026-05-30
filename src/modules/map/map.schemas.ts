import { z } from "zod";

const coordinateSchema = z.coerce.number().finite();

export const mapPlacesQuerySchema = z
  .object({
    city: z.string().trim().min(1),
    swLat: coordinateSchema,
    swLng: coordinateSchema,
    neLat: coordinateSchema,
    neLng: coordinateSchema,
    limit: z.coerce.number().int().positive().max(200).optional(),
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

export type MapPlacesQuery = z.infer<typeof mapPlacesQuerySchema>;
