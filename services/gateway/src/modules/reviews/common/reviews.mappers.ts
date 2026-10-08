import type { PlaceReview, Review } from "./reviews.schemas.js";
import type { PlaceReviewRow, ReviewRow } from "./reviews.types.js";

export function mapReviewRow(row: ReviewRow): Review {
  return {
    placeId: row.place_id,
    place: {
      id: row.place_id,
      name: row.place_name,
      rating: row.place_rating,
      numberOfReviews: row.place_reviews_count,
      primaryPhoto: row.primary_photo_path
        ? {
            path: row.primary_photo_path,
            url: row.primary_photo_url,
            width: row.primary_photo_width,
            height: row.primary_photo_height,
            source: row.primary_photo_source
          }
        : null
    },
    rating: row.rating,
    text: row.body,
    tags: row.tags,
    // Review photos ship with SLO-70.
    photos: [],
    helpfulCount: row.helpful_count,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}

export function mapPlaceReviewRow(row: PlaceReviewRow): PlaceReview {
  return {
    author: {
      displayName: row.author_display_name,
      // No avatars yet; the client draws a placeholder.
      avatarUrl: null
    },
    isMine: row.is_mine,
    rating: row.rating,
    text: row.body,
    tags: row.tags,
    // Review photos ship with SLO-70.
    photos: [],
    helpfulCount: row.helpful_count,
    createdAt: row.created_at,
    updatedAt: row.updated_at
  };
}
