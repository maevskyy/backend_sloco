import { afterEach, describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import {
  createReviewsService,
  PlaceNotFoundError,
  type ReviewInput,
  type ReviewRow,
  type ReviewsStoreContract
} from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

const authHeaders = { authorization: "Bearer valid-token" };
const knownPlaceId = 6124;
const reviewPath = (placeId: number) =>
  VersionedAppRoute.mePlaceReview.replace(":placeId", String(placeId));

// The real service over an in-memory store: one review per place, a replace
// keeps createdAt/helpfulCount, unknown places throw like the SQL store.
function createMemoryStore(): ReviewsStoreContract {
  const reviews = new Map<number, ReviewRow>();
  let clock = 0;
  const now = () => `2026-10-06T10:00:0${clock++}.000000+00:00`;

  return {
    async upsertReview(_userId, placeId, input: ReviewInput) {
      if (placeId !== knownPlaceId) {
        throw new PlaceNotFoundError(placeId);
      }

      const existing = reviews.get(placeId);
      const timestamp = now();
      const row: ReviewRow = {
        place_id: placeId,
        place_name: "Random Space",
        place_rating: 4.7,
        place_reviews_count: 160,
        primary_photo_path: "sloco_ai/2000566879812928176/00_vibe.jpg",
        primary_photo_url:
          "https://pub-x.r2.dev/sloco_ai/2000566879812928176/00_vibe.jpg",
        primary_photo_width: 1080,
        primary_photo_height: 1440,
        primary_photo_source: "vibe",
        rating: input.rating,
        body: input.text,
        tags: input.tags,
        helpful_count: existing?.helpful_count ?? 0,
        created_at: existing?.created_at ?? timestamp,
        updated_at: timestamp
      };
      reviews.set(placeId, row);
      return row;
    },
    async deleteReview(_userId, placeId) {
      if (placeId !== knownPlaceId) {
        throw new PlaceNotFoundError(placeId);
      }

      reviews.delete(placeId);
    },
    async listReviews(_userId, page) {
      const rows = [...reviews.values()];
      return {
        rows: rows.slice(page.offset, page.offset + page.limit),
        total: rows.length
      };
    }
  };
}

let app: Awaited<ReturnType<typeof buildApp>> | undefined;

async function createApp() {
  app = await buildApp({
    authService,
    reviewsService: createReviewsService(createMemoryStore())
  });
  return app;
}

afterEach(async () => {
  await app?.close();
  app = undefined;
});

describe("reviews routes", () => {
  it("returns 401 without a session", async () => {
    const server = await createApp();

    const list = await server.inject({
      method: "GET",
      url: VersionedAppRoute.meReviews
    });
    const put = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      payload: { rating: 4 }
    });

    expect(list.statusCode).toBe(401);
    expect(put.statusCode).toBe(401);
  });

  it("walks the spec's done-means flow", async () => {
    const server = await createApp();

    const empty = await server.inject({
      method: "GET",
      url: VersionedAppRoute.meReviews,
      headers: authHeaders
    });
    expect(empty.statusCode).toBe(200);
    expect(empty.json()).toEqual({ reviews: [], total: 0 });

    const created = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      headers: authHeaders,
      payload: { rating: 4, text: "ok", tags: ["cozy"], photoIds: [] }
    });
    expect(created.statusCode).toBe(200);
    const createdReview = created.json().review;
    expect(createdReview).toMatchObject({
      placeId: knownPlaceId,
      place: {
        id: knownPlaceId,
        name: "Random Space",
        rating: 4.7,
        numberOfReviews: 160,
        primaryPhoto: {
          url: "https://pub-x.r2.dev/sloco_ai/2000566879812928176/00_vibe.jpg"
        }
      },
      rating: 4,
      text: "ok",
      tags: ["cozy"],
      photos: [],
      helpfulCount: 0
    });
    expect(createdReview.updatedAt).toBe(createdReview.createdAt);

    const replaced = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      headers: authHeaders,
      payload: { rating: 5, text: "ok", tags: ["cozy"] }
    });
    expect(replaced.statusCode).toBe(200);
    expect(replaced.json().review.rating).toBe(5);
    expect(replaced.json().review.createdAt).toBe(createdReview.createdAt);
    expect(replaced.json().review.updatedAt).not.toBe(createdReview.updatedAt);

    const one = await server.inject({
      method: "GET",
      url: VersionedAppRoute.meReviews,
      headers: authHeaders
    });
    expect(one.json().total).toBe(1);
    expect(one.json().reviews).toHaveLength(1);
    expect(one.json().reviews[0].rating).toBe(5);

    const deleted = await server.inject({
      method: "DELETE",
      url: reviewPath(knownPlaceId),
      headers: authHeaders
    });
    expect(deleted.statusCode).toBe(204);

    const deletedAgain = await server.inject({
      method: "DELETE",
      url: reviewPath(knownPlaceId),
      headers: authHeaders
    });
    expect(deletedAgain.statusCode).toBe(204);

    const emptyAgain = await server.inject({
      method: "GET",
      url: VersionedAppRoute.meReviews,
      headers: authHeaders
    });
    expect(emptyAgain.json()).toEqual({ reviews: [], total: 0 });
  });

  it.each([
    ["an unknown tag", { rating: 4, tags: ["noisy"] }],
    ["rating 0", { rating: 0 }],
    ["rating 6", { rating: 6 }],
    ["a fractional rating", { rating: 4.5 }],
    ["no rating", { text: "ok" }],
    ["text over 2000 characters", { rating: 4, text: "x".repeat(2001) }],
    [
      "more than 10 photos",
      { rating: 4, photoIds: Array.from({ length: 11 }, (_, i) => `p${i}`) }
    ],
    ["a photo id the user did not upload", { rating: 4, photoIds: ["0b9c"] }]
  ])("answers 422 for %s", async (_case, payload) => {
    const server = await createApp();

    const response = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      headers: authHeaders,
      payload
    });

    expect(response.statusCode).toBe(422);
    expect(response.json()).toMatchObject({
      status: "error",
      message: "Invalid review request"
    });
    expect(response.json().issues.length).toBeGreaterThan(0);
  });

  it("accepts 2000 characters of text and drops repeated tags", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      headers: authHeaders,
      payload: { rating: 3, text: "x".repeat(2000), tags: ["cozy", "quiet", "cozy"] }
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().review.tags).toEqual(["cozy", "quiet"]);
  });

  it("answers 422 for an out-of-range page", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "GET",
      url: `${VersionedAppRoute.meReviews}?limit=101`,
      headers: authHeaders
    });

    expect(response.statusCode).toBe(422);
  });

  it("answers 404 when placeId is not a place", async () => {
    const server = await createApp();

    const put = await server.inject({
      method: "PUT",
      url: reviewPath(999999),
      headers: authHeaders,
      payload: { rating: 4 }
    });
    const del = await server.inject({
      method: "DELETE",
      url: reviewPath(999999),
      headers: authHeaders
    });

    expect(put.statusCode).toBe(404);
    expect(put.json()).toEqual({ status: "error", message: "Place not found" });
    expect(del.statusCode).toBe(404);
  });

  it("publishes the three paths and the ReviewDTO schema", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "GET",
      url: VersionedAppRoute.swaggerOpenApiJson
    });
    const spec = response.json();

    expect(spec.paths["/v1/me/reviews"].get).toBeDefined();
    expect(spec.paths["/v1/me/places/{placeId}/review"].put).toBeDefined();
    expect(spec.paths["/v1/me/places/{placeId}/review"].delete).toBeDefined();
    expect(spec.components.schemas.ReviewDTO).toBeDefined();
  });
});
