import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { MapPlacePin, MapPlacesService } from "../index.js";

const validQuery =
  `${VersionedAppRoute.mapPlaces}?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700`;

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

function mapPlace(overrides: Partial<MapPlacePin>): MapPlacePin {
  return {
    id: 1,
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
    priceLevel: null,
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
    totalPhotoCount: 0,
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
    displayKind: "featured",
    displayPriority: 1,
    ...overrides
  };
}

describe("map routes", () => {
  it("returns map places for a valid bbox query", async () => {
    const app = await buildApp({
      mapPlacesService: async () => ({
        places: [
          mapPlace({
            name: "Pane e Vino",
            country: "Germany",
            city: "Berlin",
            latitude: 52.552578,
            longitude: 13.352883,
            rating: 4,
            numberOfReviews: 17,
            googleRating: 4,
            googleUserRatingCount: 17
          })
        ]
      })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      places: [
        mapPlace({
          name: "Pane e Vino",
          country: "Germany",
          city: "Berlin",
          latitude: 52.552578,
          longitude: 13.352883,
          rating: 4,
          numberOfReviews: 17,
          googleRating: 4,
          googleUserRatingCount: 17
        })
      ]
    });
  });

  it("returns saved state for authenticated map requests", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async getSavedPlaceStates(userId: string, placeIds: number[]) {
          expect(userId).toBe(authenticatedUser.id);
          expect(placeIds).toEqual([1, 2]);
          return new Map([
            [
              2,
              {
                isSaved: true,
                collectionIds: ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"]
              }
            ]
          ]);
        }
      }),
      mapPlacesService: async () => ({
        places: [
          mapPlace({
            id: 1,
            source: "osm",
            sourceId: "osm:node/1",
            name: "First Coffee",
            country: "Romania",
            city: "Bucharest",
            latitude: 44.43,
            longitude: 26.09,
            rating: null,
            numberOfReviews: null,
            googleRating: null,
            googleUserRatingCount: null
          }),
          mapPlace({
            id: 2,
            source: "osm",
            sourceId: "osm:node/2",
            name: "Second Coffee",
            country: "Romania",
            city: "Bucharest",
            latitude: 44.44,
            longitude: 26.1,
            rating: null,
            numberOfReviews: null,
            displayKind: "dot",
            displayPriority: 2
          })
        ]
      })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().places).toMatchObject([
      {
        id: 1,
        isSaved: false,
        savedCollectionIds: [],
        displayKind: "featured",
        displayPriority: 1
      },
      {
        id: 2,
        isSaved: true,
        savedCollectionIds: ["4b572b66-d74d-49bb-b9b5-9780c266c6f7"],
        displayKind: "dot",
        displayPriority: 2
      }
    ]);
  });

  it("returns 401 when an invalid token is sent to the map endpoint", async () => {
    const app = await buildApp({
      authService,
      mapPlacesService: async () => ({
        places: []
      })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery,
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

  it("returns 400 when required query params are missing", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.mapPlaces}?swLat=52.4800&swLng=13.3300`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when bbox coordinates are invalid", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.mapPlaces}?swLat=52.5600&swLng=13.3300&neLat=52.4800&neLng=13.4700`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("passes the parsed zoom to the service", async () => {
    let capturedZoom: number | undefined;
    const mapPlacesService: MapPlacesService = async (query) => {
      capturedZoom = query.zoom;
      return {
        places: []
      };
    };
    const app = await buildApp({
      mapPlacesService
    });

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&zoom=13`
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(capturedZoom).toBe(13);
  });

  it("returns 200 when zoom is omitted", async () => {
    const app = await buildApp({
      mapPlacesService: async () => ({ places: [] })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
  });

  it("returns 400 when zoom is out of range", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&zoom=99`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when limit is over 250", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&limit=251`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 500 when the map places service fails", async () => {
    const app = await buildApp({
      mapPlacesService: async () => {
        throw new Error("Supabase query failed");
      }
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(500);
    expect(response.json()).toEqual({
      status: "error"
    });
  });

  it("does not expose unversioned map routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: AppRoute.MapPlaces
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
