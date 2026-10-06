import { buildComponentSchemas, makeDefineRoute } from "../../../config/openapi.js";
import { sharedErrorResponses } from "../../../config/http-schemas.js";
import { reviewsSchemaRegistry } from "./reviews.schemas.js";

export const reviewsComponentSchemas = buildComponentSchemas(
  reviewsSchemaRegistry
);

// This module answers invalid input with 422, as the iOS spec asks.
const reviewsErrorResponses = {
  ...sharedErrorResponses,
  422: { $ref: "ValidationErrorResponse#" }
} as const;

const defineRoute = makeDefineRoute({
  tag: "Reviews",
  errorResponses: reviewsErrorResponses
});

export const listReviewsRouteSchema = defineRoute({
  summary: "List the authenticated user's reviews.",
  description:
    "Newest first (createdAt descending). Each review carries its place card (same field names as /v1/places/{id}). limit 1..100 (default 50), offset >= 0; out of range is 422.",
  query: "ListReviewsQuery",
  ok: "ReviewsListResponse"
});

export const upsertReviewRouteSchema = defineRoute({
  summary: "Create or replace the user's review of a place.",
  description:
    "One review per user per place; a second write replaces the first, keeps createdAt and helpfulCount, and sets updatedAt. 422: rating outside 1..5, text over 2000 characters, an unknown tag, more than 10 photoIds, a photo id the user did not upload (photo upload is not available yet, so photoIds must be empty). 404: placeId is not a place.",
  params: "ReviewParams",
  body: "UpsertReviewBody",
  ok: "ReviewResponse"
});

export const deleteReviewRouteSchema = {
  tags: ["Reviews"],
  summary: "Delete the user's review of a place.",
  description:
    "Idempotent: deleting a review that does not exist is also 204. 404: placeId is not a place.",
  security: [{ bearerAuth: [] }],
  params: {
    $ref: "ReviewParams#"
  },
  response: {
    204: {
      description: "Review deleted."
    },
    ...reviewsErrorResponses
  }
} as const;
