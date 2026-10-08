import { ReviewPhotoNotFoundError } from "../common/reviews.errors.js";
import {
  mapPlaceReviewRow,
  mapReviewRow
} from "../common/reviews.mappers.js";
import type {
  ListPlaceReviewsQuery,
  ListReviewsQuery,
  UpsertReviewBody
} from "../common/reviews.schemas.js";
import type {
  ReviewsServiceContract,
  ReviewsStoreContract
} from "../common/reviews.types.js";
import { ReviewsStore } from "../stores/reviews.store.js";

export class ReviewsServiceImpl implements ReviewsServiceContract {
  constructor(private readonly store: ReviewsStoreContract) {}

  async upsertReview(userId: string, placeId: number, body: UpsertReviewBody) {
    // Photo upload is not shipped yet (SLO-70): no user has uploaded a photo,
    // so any photo id is one "the user did not upload" → 422 per the spec.
    if (body.photoIds.length > 0) {
      throw new ReviewPhotoNotFoundError(body.photoIds);
    }

    const row = await this.store.upsertReview(userId, placeId, {
      rating: body.rating,
      text: body.text,
      // Tags are a set; keep the client's order, drop repeats.
      tags: [...new Set(body.tags)]
    });

    return {
      review: mapReviewRow(row)
    };
  }

  async deleteReview(userId: string, placeId: number) {
    await this.store.deleteReview(userId, placeId);
  }

  async listReviews(userId: string, page: ListReviewsQuery) {
    const { rows, total } = await this.store.listReviews(userId, page);

    return {
      reviews: rows.map(mapReviewRow),
      total
    };
  }

  async listPlaceReviews(
    placeId: number,
    viewerId: string | null,
    page: ListPlaceReviewsQuery
  ) {
    const { rows, total } = await this.store.listPlaceReviews(
      placeId,
      viewerId,
      page
    );

    return {
      reviews: rows.map(mapPlaceReviewRow),
      total
    };
  }
}

export function createReviewsService(
  store: ReviewsStoreContract = new ReviewsStore()
) {
  return new ReviewsServiceImpl(store);
}
