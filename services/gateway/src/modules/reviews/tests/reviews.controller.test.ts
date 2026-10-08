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
const placeReviewsPath = (placeId: number) =>
  VersionedAppRoute.placeReviews.replace(":placeId", String(placeId));

const otherUserId = "5d1c2b3a-9e8f-4a7b-8c6d-1e2f3a4b5c6d";
const displayNames = new Map([[otherUserId, "Veronika Ignatenko"]]);

// The real service over an in-memory store: one review per user per place, a
// replace keeps createdAt/helpfulCount, unknown places throw like the SQL store.
function createMemoryStore() {
  const reviews = new Map<string, { userId: string; row: ReviewRow }>();
  let clock = 0;
  const now = () => `2026-10-06T10:00:0${clock++}.000000+00:00`;
  const key = (userId: string, placeId: number) => `${userId}:${placeId}`;
  const newestFirst = (a: ReviewRow, b: ReviewRow) =>
    b.created_at.localeCompare(a.created_at);

  const store: ReviewsStoreContract = {
    async upsertReview(userId, placeId, input: ReviewInput) {
      if (placeId !== knownPlaceId) {
        throw new PlaceNotFoundError(placeId);
      }

      const existing = reviews.get(key(userId, placeId))?.row;
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
      reviews.set(key(userId, placeId), { userId, row });
      return row;
    },
    async deleteReview(userId, placeId) {
      if (placeId !== knownPlaceId) {
        throw new PlaceNotFoundError(placeId);
      }

      reviews.delete(key(userId, placeId));
    },
    async listReviews(userId, page) {
      const rows = [...reviews.values()]
        .filter((review) => review.userId === userId)
        .map((review) => review.row)
        .sort(newestFirst);
      return {
        rows: rows.slice(page.offset, page.offset + page.limit),
        total: rows.length
      };
    },
    async listPlaceReviews(placeId, viewerId, page) {
      if (placeId !== knownPlaceId) {
        throw new PlaceNotFoundError(placeId);
      }

      const rows = [...reviews.values()]
        .filter((review) => review.row.place_id === placeId)
        .sort((a, b) => newestFirst(a.row, b.row))
        .map(({ userId, row }) => ({
          author_display_name: displayNames.get(userId) ?? null,
          is_mine: userId === viewerId,
          rating: row.rating,
          body: row.body,
          tags: row.tags,
          helpful_count: row.helpful_count,
          created_at: row.created_at,
          updated_at: row.updated_at
        }));
      return {
        rows: rows.slice(page.offset, page.offset + page.limit),
        total: rows.length
      };
    }
  };

  return store;
}

let app: Awaited<ReturnType<typeof buildApp>> | undefined;

async function createApp(store = createMemoryStore()) {
  app = await buildApp({
    authService,
    reviewsService: createReviewsService(store)
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

  it("walks the place reviews spec's done-means flow", async () => {
    const store = createMemoryStore();
    const server = await createApp(store);

    const empty = await server.inject({
      method: "GET",
      url: placeReviewsPath(knownPlaceId)
    });
    expect(empty.statusCode).toBe(200);
    expect(empty.json()).toEqual({ reviews: [], total: 0 });

    const put = await server.inject({
      method: "PUT",
      url: reviewPath(knownPlaceId),
      headers: authHeaders,
      payload: { rating: 4, text: "ok", tags: ["cozy"], photoIds: [] }
    });
    expect(put.statusCode).toBe(200);

    const anonymous = await server.inject({
      method: "GET",
      url: placeReviewsPath(knownPlaceId)
    });
    expect(anonymous.statusCode).toBe(200);
    expect(anonymous.json().total).toBe(1);
    const [review] = anonymous.json().reviews;
    expect(review).toMatchObject({
      author: { displayName: null, avatarUrl: null },
      isMine: false,
      rating: 4,
      text: "ok",
      tags: ["cozy"],
      photos: [],
      helpfulCount: 0
    });
    expect(Object.keys(review)).toEqual([
      "author",
      "isMine",
      "rating",
      "text",
      "tags",
      "photos",
      "helpfulCount",
      "createdAt",
      "updatedAt"
    ]);
    expect(anonymous.body).not.toContain(authenticatedUser.email);
    expect(anonymous.body).not.toContain(authenticatedUser.id);

    const mine = await server.inject({
      method: "GET",
      url: placeReviewsPath(knownPlaceId),
      headers: authHeaders
    });
    expect(mine.json().reviews[0].isMine).toBe(true);

    const deleted = await server.inject({
      method: "DELETE",
      url: reviewPath(knownPlaceId),
      headers: authHeaders
    });
    expect(deleted.statusCode).toBe(204);

    const emptyAgain = await server.inject({
      method: "GET",
      url: placeReviewsPath(knownPlaceId)
    });
    expect(emptyAgain.json()).toEqual({ reviews: [], total: 0 });
  });

  it("lists other users' reviews newest first with their names", async () => {
    const store = createMemoryStore();
    await store.upsertReview(otherUserId, knownPlaceId, {
      rating: 5,
      text: "A really nice place!",
      tags: ["romantic"]
    });
    await store.upsertReview(authenticatedUser.id, knownPlaceId, {
      rating: 3,
      text: "",
      tags: []
    });
    const server = await createApp(store);

    const response = await server.inject({
      method: "GET",
      url: `${placeReviewsPath(knownPlaceId)}?limit=1&offset=1`,
      headers: authHeaders
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      reviews: [
        {
          author: { displayName: "Veronika Ignatenko", avatarUrl: null },
          isMine: false,
          rating: 5
        }
      ],
      total: 2
    });
  });

  it("answers 401 for an invalid token on the place reviews list", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "GET",
      url: placeReviewsPath(knownPlaceId),
      headers: { authorization: "Bearer expired-token" }
    });

    expect(response.statusCode).toBe(401);
  });

  it("answers 404 for the reviews of an unknown place", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "GET",
      url: placeReviewsPath(999999999)
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({ status: "error", message: "Place not found" });
  });

  it.each([["limit=0"], ["limit=51"], ["offset=-1"], ["limit=abc"]])(
    "answers 422 for a place reviews page with %s",
    async (query) => {
      const server = await createApp();

      const response = await server.inject({
        method: "GET",
        url: `${placeReviewsPath(knownPlaceId)}?${query}`
      });

      expect(response.statusCode).toBe(422);
    }
  );

  it("publishes the review paths and the DTO schemas", async () => {
    const server = await createApp();

    const response = await server.inject({
      method: "GET",
      url: VersionedAppRoute.swaggerOpenApiJson
    });
    const spec = response.json();

    expect(spec.paths["/v1/me/reviews"].get).toBeDefined();
    expect(spec.paths["/v1/me/places/{placeId}/review"].put).toBeDefined();
    expect(spec.paths["/v1/me/places/{placeId}/review"].delete).toBeDefined();
    expect(spec.paths["/v1/places/{placeId}/reviews"].get).toBeDefined();
    expect(spec.components.schemas.ReviewDTO).toBeDefined();
    expect(spec.components.schemas.PlaceReviewDTO).toBeDefined();
  });
});
