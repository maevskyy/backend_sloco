import { z } from "zod";
import { placeBucketsQuerySchema } from "../../places/index.js";

const coordinateSchema = z.coerce.number().finite();
const optionalContextSchema = z.string().trim().min(1).max(100).optional();

const debugQuerySchema = z
  .enum(["true", "false"])
  .default("false")
  .transform((value) => value === "true");

export const feedSortSchema = z.enum(["relevance", "distance"]);

export const feedPlacesQuerySchema = z
  .object({
    limit: z.coerce.number().int().min(1).max(50).default(20),
    offset: z.coerce.number().int().min(0).default(0),
    lat: coordinateSchema.optional(),
    lng: coordinateSchema.optional(),
    city: optionalContextSchema.describe(
      "Hard cut: only places whose city matches (unaccent, case-insensitive). Unknown names return an empty page, not the unscoped ranking. Does not change taste clustering."
    ),
    country: optionalContextSchema,
    sort: feedSortSchema.default("relevance"),
    category: placeBucketsQuerySchema,
    debug: debugQuerySchema
  })
  .refine((query) => (query.lat === undefined) === (query.lng === undefined), {
    message: "lat and lng must be sent together",
    path: ["lat"]
  })
  .refine((query) => query.sort !== "distance" || query.lat !== undefined, {
    message: "sort=distance requires lat and lng",
    path: ["sort"]
  });

export const feedPrimaryPhotoSchema = z.object({
  path: z.string(),
  url: z.string().nullable(),
  width: z.number().int().nullable(),
  height: z.number().int().nullable(),
  source: z.string().nullable()
});

export const feedPersonalizationStatusSchema = z.enum([
  "personalized",
  "anonymous_fallback",
  "no_signals_fallback",
  "empty_recommendation_fallback",
  "recommendation_service_fallback"
]);

export const feedCacheStatusSchema = z.enum([
  "hit",
  "miss",
  "bypass",
  "not_applicable"
]);

export const feedMetaSchema = z.object({
  personalizationStatus: feedPersonalizationStatusSchema,
  cacheStatus: feedCacheStatusSchema,
  sort: feedSortSchema,
  algorithmVersion: z.string().nullable(),
  embeddingRunId: z.string().nullable(),
  requestId: z
    .uuid()
    .nullable()
    .describe(
      "Serving id from the recommender — put it (with each card's position) into telemetry events. One id per recommendation snapshot: pages and re-sorts of the same snapshot share it. Null on fallback feeds."
    ),
  generatedAt: z.string(),
  expiresAt: z.string().nullable()
});

export const feedInputSummarySchema = z.object({
  favouritesCount: z.number().int().min(0),
  wantToGoCount: z.number().int().min(0),
  validInputCount: z.number().int().min(0),
  invalidPlaceIds: z.array(z.string())
});

export const feedPlaceCardSchema = z.object({
  id: z.number().int(),
  source: z.string(),
  sourceId: z.string(),
  name: z.string(),
  country: z.string(),
  city: z.string(),
  category: z.string(),
  primaryType: z.string().nullable(),
  latitude: z.number(),
  longitude: z.number(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(0).max(4).nullable(),
  numberOfReviews: z.number().int().nullable(),
  mapVisibilityScore: z.number(),
  matchScore: z.number().int().min(0).max(100),
  rank: z.number().int().min(1),
  position: z
    .number()
    .int()
    .min(0)
    .nullable()
    .describe(
      "0-based position in the recommender's snapshot (stable under sort= and category=; rank is positional per page instead). Echo it in telemetry events together with feed.requestId. Null on fallback feeds."
    ),
  whyRecommended: z.string(),
  blurb: z.string(),
  tags: z.array(z.string()),
  distanceMeters: z.number().nullable(),
  primaryPhoto: feedPrimaryPhotoSchema.nullable(),
  isSaved: z.boolean(),
  reaction: z.enum(["favorite", "dislike", "hide"]).nullable()
});

export const feedPlacesResponseSchema = z.object({
  feed: feedMetaSchema,
  inputSummary: feedInputSummarySchema,
  places: z.array(feedPlaceCardSchema)
});

export const feedSchemaRegistry = z.registry<{ id: string }>();

feedSchemaRegistry.add(feedPlacesQuerySchema, { id: "FeedPlacesQuery" });
feedSchemaRegistry.add(feedSortSchema, { id: "FeedSort" });
feedSchemaRegistry.add(feedPrimaryPhotoSchema, { id: "FeedPrimaryPhoto" });
feedSchemaRegistry.add(feedPersonalizationStatusSchema, {
  id: "FeedPersonalizationStatus"
});
feedSchemaRegistry.add(feedCacheStatusSchema, { id: "FeedCacheStatus" });
feedSchemaRegistry.add(feedMetaSchema, { id: "FeedMeta" });
feedSchemaRegistry.add(feedInputSummarySchema, { id: "FeedInputSummary" });
feedSchemaRegistry.add(feedPlaceCardSchema, { id: "FeedPlaceCard" });
feedSchemaRegistry.add(feedPlacesResponseSchema, {
  id: "FeedPlacesResponse"
});

export type FeedPlacesQuery = z.infer<typeof feedPlacesQuerySchema>;
