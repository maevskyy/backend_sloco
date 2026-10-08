import type {
  ListPlaceReviewsQuery,
  ListReviewsQuery,
  PlaceReviewsListResponse,
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

// One review of a place's list, with its author's profile name. The author's
// user_id stays in SQL: is_mine is computed there against the caller.
export type PlaceReviewRow = {
  author_display_name: string | null;
  is_mine: boolean;
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

export type PlaceReviewRowsPage = {
  rows: PlaceReviewRow[];
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
  // Every user's review of one place; viewerId (null when anonymous) only
  // sets is_mine. Throws PlaceNotFoundError when placeId is not a place.
  listPlaceReviews(
    placeId: number,
    viewerId: string | null,
    page: ListPlaceReviewsQuery
  ): Promise<PlaceReviewRowsPage>;
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
  listPlaceReviews(
    placeId: number,
    viewerId: string | null,
    page: ListPlaceReviewsQuery
  ): Promise<PlaceReviewsListResponse>;
};
