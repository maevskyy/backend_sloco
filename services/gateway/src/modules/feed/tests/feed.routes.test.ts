import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { FeedPlaceCard, FeedPlacesService } from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

function feedPlace(overrides: Partial<FeedPlaceCard> = {}): FeedPlaceCard {
  return {
    id: 123,
    source: "google",
    sourceId: "ChIJ123",
    name: "Origo Coffee",
    country: "RO",
    city: "Bucharest",
    category: "cafe",
    primaryType: "coffee_shop",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    priceLevel: 2,
    numberOfReviews: 120,
    mapVisibilityScore: 91,
    matchScore: 94,
    rank: 1,
    whyRecommended: "Because this matches places you saved.",
    blurb: "Calm specialty coffee spot.",
    tags: ["quiet", "coffee"],
    distanceMeters: null,
    primaryPhoto: null,
    isSaved: false,
    reaction: null,
    ...overrides
  };
}

function feedService(): FeedPlacesService {
  return async ({ query, user }) => {
    expect(query.limit).toBe(20);
    expect(query.offset).toBe(0);
    expect(query.sort).toBe("relevance");
    expect(query.debug).toBe(false);

    return {
      feed: {
        personalizationStatus: user ? "personalized" : "anonymous_fallback",
        cacheStatus: "not_applicable",
        sort: query.sort,
        algorithmVersion: "test",
        embeddingRunId: null,
        generatedAt: "2026-06-01T10:00:00.000Z",
        expiresAt: null
      },
      inputSummary: {
        favouritesCount: user ? 1 : 0,
        wantToGoCount: 0,
        validInputCount: user ? 1 : 0,
        invalidPlaceIds: []
      },
      places: [feedPlace()]
    };
  };
}

describe("feed routes", () => {
  it("returns a fallback feed without auth", async () => {
    const app = await buildApp({
      feedPlacesService: feedService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.feedPlaces
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      feed: {
        personalizationStatus: "anonymous_fallback"
      },
      places: [
        {
          id: 123,
          name: "Origo Coffee",
          matchScore: 94
        }
      ]
    });
  });

  it("passes valid auth to the feed service", async () => {
    const app = await buildApp({
      authService,
      feedPlacesService: feedService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.feedPlaces,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().feed.personalizationStatus).toBe("personalized");
  });

  it("passes offset through to the feed service", async () => {
    const app = await buildApp({
      feedPlacesService: async ({ query }) => {
        expect(query.limit).toBe(10);
        expect(query.offset).toBe(30);

        return {
          feed: {
            personalizationStatus: "anonymous_fallback",
            cacheStatus: "not_applicable",
            sort: query.sort,
            algorithmVersion: "test",
            embeddingRunId: null,
            generatedAt: "2026-06-01T10:00:00.000Z",
            expiresAt: null
          },
          inputSummary: {
            favouritesCount: 0,
            wantToGoCount: 0,
            validInputCount: 0,
            invalidPlaceIds: []
          },
          places: [feedPlace()]
        };
      }
    });

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?limit=10&offset=30`
    });

    await app.close();

    expect(response.statusCode).toBe(200);
  });

  it("returns 401 for invalid auth", async () => {
    const app = await buildApp({
      authService,
      feedPlacesService: feedService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.feedPlaces,
      headers: {
        authorization: "Bearer invalid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({
      status: "error",
      message: "Unauthorized"
    });
  });

  it("returns 400 for invalid query params", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?limit=99`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when only one coordinate is sent", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?lat=44.43`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 for an unknown sort value", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?sort=nearest&lat=44.43&lng=26.1`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 for sort=distance without coordinates", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?sort=distance`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("passes sort=distance through and echoes it in the meta", async () => {
    const app = await buildApp({
      feedPlacesService: async ({ query }) => {
        expect(query.sort).toBe("distance");
        expect(query.lat).toBe(44.43);
        expect(query.lng).toBe(26.1);

        return {
          feed: {
            personalizationStatus: "anonymous_fallback",
            cacheStatus: "not_applicable",
            sort: query.sort,
            algorithmVersion: "test",
            embeddingRunId: null,
            generatedAt: "2026-06-01T10:00:00.000Z",
            expiresAt: null
          },
          inputSummary: {
            favouritesCount: 0,
            wantToGoCount: 0,
            validInputCount: 0,
            invalidPlaceIds: []
          },
          places: [feedPlace({ distanceMeters: 120 })]
        };
      }
    });

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.feedPlaces}?sort=distance&lat=44.43&lng=26.1`
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().feed.sort).toBe("distance");
    expect(response.json().places[0].distanceMeters).toBe(120);
  });

  it("does not expose unversioned feed routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: AppRoute.FeedPlaces
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
