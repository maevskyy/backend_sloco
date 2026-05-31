import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { PlaceDetails, PlaceDetailsService } from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

function createSavedPlacesService(
  overrides: Partial<SavedPlacesService>
): SavedPlacesService {
  const unused = async () => {
    throw new Error("not used");
  };

  return {
    getSavedDashboard: unused,
    getCollectionDetail: unused,
    savePlace: unused,
    unsavePlace: unused,
    createCollection: unused,
    updateCollection: unused,
    deleteCollection: unused,
    addPlaceToCollection: unused,
    removePlaceFromCollection: unused,
    reorderCollectionPlaces: unused,
    getSavedPlaceIds: unused,
    getSavedPlaceStates: unused,
    ...overrides
  } as SavedPlacesService;
}

function placeDetails(overrides: Partial<PlaceDetails> = {}): PlaceDetails {
  return {
    id: 123,
    source: "google",
    sourceId: "ChIJ123",
    name: "Seneca Anticafe",
    country: "romania",
    city: "bucharest",
    category: "cafe",
    primaryType: "cafe",
    types: ["cafe", "food"],
    latitude: 44.43,
    longitude: 26.1,
    formattedAddress: "Bucharest",
    shortFormattedAddress: "Bucharest",
    businessStatus: "OPERATIONAL",
    googleMapsUri: "https://maps.google.com/example",
    phone: "+40 123",
    internationalPhone: "+40 123",
    websiteUrl: "https://example.com",
    rating: 4.8,
    priceLevel: 2,
    numberOfReviews: 1411,
    googleRating: 4.8,
    googleUserRatingCount: 1411,
    apifyReviewCount: null,
    apifyRatingAvg: null,
    ratingCountForScore: 1411,
    bayesianRating: 4.7,
    ratingScore: 92,
    popularityScore: 80,
    ratingConfidenceScore: 95,
    priceMinRon: null,
    priceMaxRon: null,
    mapVisibilityScore: 89,
    mapVisibilityRank: 1,
    mapMinZoomGlobal: 12,
    aiCardSummary: "Calm coffee and work spot.",
    aiPlaceTypeSummary: "Cafe",
    aiVibe: "calm",
    aiWhatToExpect: "Quiet tables.",
    aiFoodAndDrinks: "Coffee.",
    aiPrice: "mid",
    aiService: "friendly",
    aiTheMove: "Go daytime.",
    aiWatchOut: null,
    aiTags: ["calm", "coffee"],
    aiTagsJson: [],
    aiConfidence: 0.8,
    axisQuietLively: 20,
    axisWorkSocial: 40,
    axisDayNight: 30,
    axisCasualPremium: 40,
    axisDrinksFood: 70,
    axisLocalTourist: 30,
    axisCheapExpensive: 50,
    axisTraditionalExperimental: 45,
    reviewPhotoCount: 1,
    vibePhotoCount: 2,
    primaryPhoto: null,
    totalPhotoCount: 3,
    openingHours: { openNow: true },
    serves: ["coffee"],
    features: { dineIn: true },
    googleDetails: {},
    apifyDetails: {},
    aiDetails: {},
    photoDetails: {},
    rawCuisineStyle: null,
    isSaved: false,
    savedCollectionIds: [],
    ...overrides
  };
}

describe("place details routes", () => {
  it("returns place details without auth", async () => {
    const app = await buildApp({
      placeDetailsService: async () => ({
        place: placeDetails()
      })
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.place.replace(":placeId", "123")
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      place: placeDetails()
    });
  });

  it("enriches place details with saved state for valid auth", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async getSavedPlaceStates(userId: string, placeIds: number[]) {
          expect(userId).toBe(authenticatedUser.id);
          expect(placeIds).toEqual([123]);
          return new Map([
            [
              123,
              {
                isSaved: true,
                collectionIds: ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"]
              }
            ]
          ]);
        }
      }),
      placeDetailsService: async () => ({
        place: placeDetails()
      })
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.place.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().place).toMatchObject({
      id: 123,
      isSaved: true,
      savedCollectionIds: ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"]
    });
  });

  it("returns 401 for invalid auth", async () => {
    const app = await buildApp({
      authService,
      placeDetailsService: async () => ({
        place: placeDetails()
      })
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.place.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer invalid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
  });

  it("returns 404 when the place does not exist", async () => {
    const service: PlaceDetailsService = async () => null;
    const app = await buildApp({
      placeDetailsService: service
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.place.replace(":placeId", "999")
    });

    await app.close();

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({
      status: "error",
      message: "Place not found"
    });
  });

  it("returns 400 for invalid place ids", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.place.replace(":placeId", "0")
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("does not expose unversioned place detail routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: AppRoute.Place.replace(":placeId", "123")
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
