import type {
  ListReviewsQuery,
  ReviewResponse,
  ReviewTag,
  ReviewsListResponse,
  UpsertReviewBody
} from "./reviews.schemas.js";

// One review joined with its place card (places + the primary place_photos row).
export type ReviewRow = {
  place_id: number;
  place_name: string;
  place_rating: number | null;
  place_reviews_count: number | null;
  primary_photo_path: string | null;
  primary_photo_url: string | null;
  primary_photo_width: number | null;
  primary_photo_height: number | null;
  primary_photo_source: string | null;
  rating: number;
  body: string;
  tags: ReviewTag[];
  helpful_count: number;
  created_at: string;
  updated_at: string;
};

export type ReviewInput = {
  rating: number;
  text: string;
  tags: ReviewTag[];
};

export type ReviewRowsPage = {
  rows: ReviewRow[];
  total: number;
};

export type ReviewsStoreContract = {
  // Throws PlaceNotFoundError when placeId is not a place.
  upsertReview(
    userId: string,
    placeId: number,
    input: ReviewInput
  ): Promise<ReviewRow>;
  // Throws PlaceNotFoundError when placeId is not a place; a missing review
  // is not an error.
  deleteReview(userId: string, placeId: number): Promise<void>;
  listReviews(userId: string, page: ListReviewsQuery): Promise<ReviewRowsPage>;
};

export type ReviewsServiceContract = {
  upsertReview(
    userId: string,
    placeId: number,
    body: UpsertReviewBody
  ): Promise<ReviewResponse>;
  deleteReview(userId: string, placeId: number): Promise<void>;
  listReviews(
    userId: string,
    page: ListReviewsQuery
  ): Promise<ReviewsListResponse>;
};
