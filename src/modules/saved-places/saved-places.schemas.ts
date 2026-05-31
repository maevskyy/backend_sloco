import { z } from "zod";

export const savedPlaceParamsSchema = z.object({
  placeId: z.coerce.number().int().positive()
});

export const savedCollectionParamsSchema = z.object({
  collectionId: z.string().uuid()
});

export const savedCollectionPlaceParamsSchema =
  savedCollectionParamsSchema.merge(savedPlaceParamsSchema);

export const savePlaceBodySchema = z.object({
  placeId: z.number().int().positive(),
  collectionIds: z.array(z.string().uuid()).optional()
});

export const createSavedCollectionBodySchema = z.object({
  name: z.string().trim().min(1).max(80),
  colorHex: z
    .string()
    .trim()
    .regex(/^#[0-9a-fA-F]{6}$/)
    .optional()
});

export const updateSavedCollectionBodySchema = z
  .object({
    name: z.string().trim().min(1).max(80).optional(),
    colorHex: z
      .string()
      .trim()
      .regex(/^#[0-9a-fA-F]{6}$/)
      .nullable()
      .optional(),
    sortOrder: z.number().int().min(0).optional()
  })
  .refine((body) => Object.keys(body).length > 0, {
    message: "At least one collection field must be provided"
  });

export const addPlaceToCollectionBodySchema = z.object({
  placeId: z.number().int().positive()
});

export const reorderCollectionPlacesBodySchema = z.object({
  placeIds: z.array(z.number().int().positive())
});

export type SavedPlaceParams = z.infer<typeof savedPlaceParamsSchema>;
export type SavedCollectionParams = z.infer<
  typeof savedCollectionParamsSchema
>;
export type SavedCollectionPlaceParams = z.infer<
  typeof savedCollectionPlaceParamsSchema
>;
export type SavePlaceBody = z.infer<typeof savePlaceBodySchema>;
export type CreateSavedCollectionBody = z.infer<
  typeof createSavedCollectionBodySchema
>;
export type UpdateSavedCollectionBody = z.infer<
  typeof updateSavedCollectionBodySchema
>;
export type AddPlaceToCollectionBody = z.infer<
  typeof addPlaceToCollectionBodySchema
>;
export type ReorderCollectionPlacesBody = z.infer<
  typeof reorderCollectionPlacesBodySchema
>;
