import { z } from "zod";
import { placePrimaryPhotoSchema } from "../../places/index.js";

// The fixed vocabulary from the iOS spec (USER_REVIEWS_API.md). Validated here,
// not by a database check, so a new tag is a code change, not a migration.
export const REVIEW_TAGS = [
  "romantic",
  "trendy",
  "lively",
  "quiet",
  "social",
  "relaxed",
  "cozy"
] as const;

export const REVIEW_TEXT_MAX_LENGTH = 2000;
export const REVIEW_PHOTOS_MAX = 10;

export const reviewTagSchema = z.enum(REVIEW_TAGS);

export const reviewParamsSchema = z.object({
  placeId: z.coerce.number().int().min(1)
});

export const listReviewsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(50),
  offset: z.coerce.number().int().min(0).default(0)
});

// A place's reviews page smaller than the user's own list: the iOS spec
// (PLACE_REVIEWS_API.md) asks for 1..50, default 20.
export const listPlaceReviewsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(50).default(20),
  offset: z.coerce.number().int().min(0).default(0)
});

export const upsertReviewBodySchema = z.object({
  rating: z.number().int().min(1).max(5),
  text: z.string().max(REVIEW_TEXT_MAX_LENGTH).default(""),
  tags: z.array(reviewTagSchema).default([]),
  photoIds: z.array(z.string()).max(REVIEW_PHOTOS_MAX).default([])
});

// Same field names and shapes as /v1/places/{id}, so the list draws each card
// without one details request per review.
export const reviewPlaceSchema = z.object({
  id: z.number().int(),
  name: z.string(),
  rating: z.number().nullable(),
  numberOfReviews: z.number().int().nullable(),
  primaryPhoto: placePrimaryPhotoSchema.nullable()
});

export const reviewPhotoSchema = z.object({
  id: z.string(),
  url: z.string()
});

export const reviewSchema = z.object({
  placeId: z.number().int(),
  place: reviewPlaceSchema,
  rating: z.number().int().min(1).max(5),
  text: z.string(),
  tags: z.array(reviewTagSchema),
  photos: z.array(reviewPhotoSchema),
  helpfulCount: z.number().int(),
  createdAt: z.string(),
  updatedAt: z.string()
});

export const reviewResponseSchema = z.object({
  review: reviewSchema
});

export const reviewsListResponseSchema = z.object({
  reviews: z.array(reviewSchema),
  total: z.number().int()
});

// The author of a review on a place's list. Never the email or the user id:
// displayName is profiles.display_name (as on /v1/me), null when unset.
export const placeReviewAuthorSchema = z.object({
  displayName: z.string().nullable(),
  avatarUrl: z.string().nullable()
});

// One review on a place's list: the ReviewDTO fields without the place card,
// plus the author and whether the caller wrote it.
export const placeReviewSchema = z.object({
  author: placeReviewAuthorSchema,
  isMine: z.boolean(),
  ...reviewSchema.omit({ placeId: true, place: true }).shape
});

export const placeReviewsListResponseSchema = z.object({
  reviews: z.array(placeReviewSchema),
  total: z.number().int()
});

export const reviewsSchemaRegistry = z.registry<{ id: string }>();

reviewsSchemaRegistry.add(reviewParamsSchema, { id: "ReviewParams" });
reviewsSchemaRegistry.add(listReviewsQuerySchema, { id: "ListReviewsQuery" });
reviewsSchemaRegistry.add(listPlaceReviewsQuerySchema, {
  id: "ListPlaceReviewsQuery"
});
reviewsSchemaRegistry.add(upsertReviewBodySchema, { id: "UpsertReviewBody" });
reviewsSchemaRegistry.add(reviewPlaceSchema, { id: "ReviewPlace" });
reviewsSchemaRegistry.add(reviewPhotoSchema, { id: "ReviewPhoto" });
// "ReviewDTO" is the name the iOS spec asks for.
reviewsSchemaRegistry.add(reviewSchema, { id: "ReviewDTO" });
reviewsSchemaRegistry.add(reviewResponseSchema, { id: "ReviewResponse" });
reviewsSchemaRegistry.add(reviewsListResponseSchema, {
  id: "ReviewsListResponse"
});
reviewsSchemaRegistry.add(placeReviewAuthorSchema, { id: "PlaceReviewAuthor" });
reviewsSchemaRegistry.add(placeReviewSchema, { id: "PlaceReviewDTO" });
reviewsSchemaRegistry.add(placeReviewsListResponseSchema, {
  id: "PlaceReviewsListResponse"
});

export type ReviewTag = z.infer<typeof reviewTagSchema>;
export type ListReviewsQuery = z.infer<typeof listReviewsQuerySchema>;
export type ListPlaceReviewsQuery = z.infer<typeof listPlaceReviewsQuerySchema>;
export type UpsertReviewBody = z.infer<typeof upsertReviewBodySchema>;
export type Review = z.infer<typeof reviewSchema>;
export type ReviewResponse = z.infer<typeof reviewResponseSchema>;
export type ReviewsListResponse = z.infer<typeof reviewsListResponseSchema>;
export type PlaceReview = z.infer<typeof placeReviewSchema>;
export type PlaceReviewsListResponse = z.infer<
  typeof placeReviewsListResponseSchema
>;
