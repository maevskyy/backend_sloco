import { z } from "zod";

const jsonObjectSchema = z.record(z.string(), z.unknown()).nullable();

export const placeDetailsParamsSchema = z.object({
  placeId: z.coerce.number().int().min(1)
});

export const placePrimaryPhotoSchema = z.object({
  path: z.string(),
  url: z.string().nullable(),
  width: z.number().int().nullable(),
  height: z.number().int().nullable(),
  source: z.string().nullable()
});

export const placeDetailsSchema = z.object({
  id: z.number().int(),
  source: z.string(),
  sourceId: z.string(),
  name: z.string(),
  country: z.string(),
  city: z.string(),
  category: z.string(),
  primaryType: z.string().nullable(),
  types: z.array(z.string()),
  latitude: z.number(),
  longitude: z.number(),
  formattedAddress: z.string().nullable(),
  shortFormattedAddress: z.string().nullable(),
  businessStatus: z.string().nullable(),
  googleMapsUri: z.string().nullable(),
  phone: z.string().nullable(),
  internationalPhone: z.string().nullable(),
  websiteUrl: z.string().nullable(),
  rating: z.number().nullable(),
  priceLevel: z.number().int().min(0).max(4).nullable(),
  numberOfReviews: z.number().int().nullable(),
  googleRating: z.number().nullable(),
  googleUserRatingCount: z.number().int().nullable(),
  apifyReviewCount: z.number().int().nullable(),
  apifyRatingAvg: z.number().nullable(),
  ratingCountForScore: z.number().int().nullable(),
  bayesianRating: z.number().nullable(),
  ratingScore: z.number().nullable(),
  popularityScore: z.number().nullable(),
  ratingConfidenceScore: z.number().nullable(),
  priceMinRon: z.number().nullable(),
  priceMaxRon: z.number().nullable(),
  mapVisibilityScore: z.number(),
  mapVisibilityRank: z.number().int().nullable(),
  mapMinZoomGlobal: z.number().int().nullable(),
  aiCardSummary: z.string().nullable(),
  aiPlaceTypeSummary: z.string().nullable(),
  aiVibe: z.string().nullable(),
  aiWhatToExpect: z.string().nullable(),
  aiFoodAndDrinks: z.string().nullable(),
  aiPrice: z.string().nullable(),
  aiService: z.string().nullable(),
  aiTheMove: z.string().nullable(),
  aiWatchOut: z.string().nullable(),
  aiTags: z.array(z.string()),
  aiTagsJson: z.unknown().nullable(),
  aiConfidence: z.number().nullable(),
  axisQuietLively: z.number().int().nullable(),
  axisWorkSocial: z.number().int().nullable(),
  axisDayNight: z.number().int().nullable(),
  axisCasualPremium: z.number().int().nullable(),
  axisDrinksFood: z.number().int().nullable(),
  axisLocalTourist: z.number().int().nullable(),
  axisCheapExpensive: z.number().int().nullable(),
  axisTraditionalExperimental: z.number().int().nullable(),
  reviewPhotoCount: z.number().int(),
  vibePhotoCount: z.number().int(),
  primaryPhoto: placePrimaryPhotoSchema.nullable(),
  totalPhotoCount: z.number().int(),
  openingHours: z.unknown().nullable(),
  serves: z.unknown().nullable(),
  features: z.unknown().nullable(),
  googleDetails: jsonObjectSchema,
  apifyDetails: jsonObjectSchema,
  aiDetails: jsonObjectSchema,
  photoDetails: jsonObjectSchema,
  rawCuisineStyle: z.string().nullable(),
  isSaved: z.boolean(),
  savedCollectionIds: z.array(z.string().uuid())
});

export const placeDetailsResponseSchema = z.object({
  place: placeDetailsSchema
});

export const placesSchemaRegistry = z.registry<{ id: string }>();

placesSchemaRegistry.add(placeDetailsParamsSchema, { id: "PlaceDetailsParams" });
placesSchemaRegistry.add(placePrimaryPhotoSchema, { id: "PlacePrimaryPhoto" });
placesSchemaRegistry.add(placeDetailsSchema, { id: "PlaceDetails" });
placesSchemaRegistry.add(placeDetailsResponseSchema, {
  id: "PlaceDetailsResponse"
});

export type PlaceDetailsParams = z.infer<typeof placeDetailsParamsSchema>;
