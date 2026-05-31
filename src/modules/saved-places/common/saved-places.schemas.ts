import { z } from "zod";

// ---------------------------------------------------------------------------
// Request schemas (also drive request validation via `.parse()` in the
// controller). These are the single source of truth for request shapes.
// ---------------------------------------------------------------------------

export const savedPlaceParamsSchema = z.object({
  placeId: z.coerce.number().int().min(1)
});

export const savedCollectionParamsSchema = z.object({
  collectionId: z.string().uuid()
});

export const savedCollectionPlaceParamsSchema =
  savedCollectionParamsSchema.merge(savedPlaceParamsSchema);

export const savePlaceBodySchema = z.object({
  placeId: z.number().int().min(1),
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
  placeId: z.number().int().min(1)
});

export const reorderCollectionPlacesBodySchema = z.object({
  placeIds: z.array(z.number().int().min(1))
});

// ---------------------------------------------------------------------------
// Response schemas. These are the single source of truth for response shapes:
// the HTTP DTO types (saved-places.types.ts) are inferred from them and the
// OpenAPI components (saved-places.openapi.ts) are generated from them.
// ---------------------------------------------------------------------------

export const savedCategorySchema = z.enum([
  "food",
  "cafe",
  "bar",
  "nature",
  "culture",
  "music",
  "other"
]);

export const savedPlaceSummarySchema = z.object({
  id: z.number().int(),
  source: z.string(),
  sourceId: z.string(),
  name: z.string(),
  city: z.string(),
  country: z.string(),
  latitude: z.number(),
  longitude: z.number(),
  category: savedCategorySchema,
  categoryLabel: z.string(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(0).max(4).nullable(),
  tags: z.array(z.string()),
  distanceText: z.string().nullable(),
  imageUrl: z.string().nullable(),
  savedAt: z.string(),
  lastViewedAt: z.string().nullable()
});

export const savedCollectionSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  colorHex: z.string().nullable(),
  placeCount: z.number().int().min(0),
  placeIds: z.array(z.number().int()),
  previewPlaces: z.array(savedPlaceSummarySchema),
  createdAt: z.string(),
  updatedAt: z.string(),
  isDefault: z.boolean(),
  sortOrder: z.number().int()
});

export const savedCollectionDetailSchema = savedCollectionSchema.omit({
  previewPlaces: true
});

export const savedCollectionCompactSchema = savedCollectionSchema.pick({
  id: true,
  name: true,
  colorHex: true,
  placeCount: true
});

export const savedDashboardResponseSchema = z.object({
  summary: z.object({
    savedPlaceCount: z.number().int().min(0),
    collectionCount: z.number().int().min(0),
    recommendationsUseSavedPlaces: z.literal(true)
  }),
  collections: z.array(savedCollectionSchema),
  recentlySaved: z.array(savedPlaceSummarySchema)
});

export const savedCollectionDetailResponseSchema = z.object({
  collection: savedCollectionDetailSchema,
  places: z.array(savedPlaceSummarySchema),
  availableCollections: z.array(savedCollectionCompactSchema)
});

export const savePlaceResponseSchema = z.object({
  placeId: z.number().int(),
  isSaved: z.literal(true),
  collectionIds: z.array(z.string().uuid()),
  savedAt: z.string()
});

export const unsavePlaceResponseSchema = z.object({
  placeId: z.number().int(),
  isSaved: z.literal(false),
  collectionIds: z.array(z.string().uuid()).max(0)
});

export const savedCollectionResponseSchema = z.object({
  collection: savedCollectionSchema
});

export const deleteCollectionResponseSchema = z.object({
  collectionId: z.string().uuid(),
  deleted: z.literal(true)
});

export const removePlaceFromCollectionResponseSchema = z.object({
  collectionId: z.string().uuid(),
  placeId: z.number().int(),
  removed: z.literal(true)
});

export const reorderCollectionPlacesResponseSchema = z.object({
  collectionId: z.string().uuid(),
  placeIds: z.array(z.number().int())
});

export const notFoundResponseSchema = z.object({
  status: z.literal("error"),
  message: z.string()
});

// ---------------------------------------------------------------------------
// OpenAPI component registry. The `id` becomes the OpenAPI component name and
// the `$id`/`$ref` used by Fastify. Keep ids stable — they are part of the
// published API contract. saved-places.openapi.ts turns this into JSON Schema.
// ---------------------------------------------------------------------------

export const savedPlacesSchemaRegistry = z.registry<{ id: string }>();

savedPlacesSchemaRegistry.add(savedPlaceParamsSchema, { id: "SavedPlaceParams" });
savedPlacesSchemaRegistry.add(savedCollectionParamsSchema, {
  id: "SavedCollectionParams"
});
savedPlacesSchemaRegistry.add(savedCollectionPlaceParamsSchema, {
  id: "SavedCollectionPlaceParams"
});
savedPlacesSchemaRegistry.add(savePlaceBodySchema, { id: "SavePlaceBody" });
savedPlacesSchemaRegistry.add(createSavedCollectionBodySchema, {
  id: "SavedCollectionBody"
});
savedPlacesSchemaRegistry.add(updateSavedCollectionBodySchema, {
  id: "UpdateSavedCollectionBody"
});
savedPlacesSchemaRegistry.add(addPlaceToCollectionBodySchema, {
  id: "AddPlaceToCollectionBody"
});
savedPlacesSchemaRegistry.add(reorderCollectionPlacesBodySchema, {
  id: "ReorderCollectionPlacesBody"
});
savedPlacesSchemaRegistry.add(savedPlaceSummarySchema, {
  id: "SavedPlaceSummary"
});
savedPlacesSchemaRegistry.add(savedCollectionSchema, { id: "SavedCollection" });
savedPlacesSchemaRegistry.add(savedCollectionDetailSchema, {
  id: "SavedCollectionDetail"
});
savedPlacesSchemaRegistry.add(savedCollectionCompactSchema, {
  id: "SavedCollectionCompact"
});
savedPlacesSchemaRegistry.add(savedDashboardResponseSchema, {
  id: "SavedDashboardResponse"
});
savedPlacesSchemaRegistry.add(savedCollectionDetailResponseSchema, {
  id: "SavedCollectionDetailResponse"
});
savedPlacesSchemaRegistry.add(savePlaceResponseSchema, {
  id: "SavePlaceResponse"
});
savedPlacesSchemaRegistry.add(unsavePlaceResponseSchema, {
  id: "UnsavePlaceResponse"
});
savedPlacesSchemaRegistry.add(savedCollectionResponseSchema, {
  id: "SavedCollectionResponse"
});
savedPlacesSchemaRegistry.add(deleteCollectionResponseSchema, {
  id: "DeleteSavedCollectionResponse"
});
savedPlacesSchemaRegistry.add(removePlaceFromCollectionResponseSchema, {
  id: "RemovePlaceFromCollectionResponse"
});
savedPlacesSchemaRegistry.add(reorderCollectionPlacesResponseSchema, {
  id: "ReorderCollectionPlacesResponse"
});
savedPlacesSchemaRegistry.add(notFoundResponseSchema, { id: "NotFoundResponse" });

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
