import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { SearchPlaceResult, SearchPlacesService } from "../index.js";

const validQuery = `${VersionedAppRoute.searchPlaces}?q=coffee&lat=44.43&lng=26.10&city=Bucharest&country=RO`;

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

function searchPlace(overrides: Partial<SearchPlaceResult> = {}): SearchPlaceResult {
  return {
    id: 1,
    name: "Origo Coffee",
    category: "cafe",
    primaryType: "cafe",
    city: "Bucharest",
    country: "RO",
    formattedAddress: "Bucharest, Romania",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    priceLevel: 2,
    primaryPhoto: null,
    distanceMeters: 830,
    matchReason: "name",
    isSaved: false,
    ...overrides
  };
}

describe("search routes", () => {
  it("returns search places for a valid query", async () => {
    const service: SearchPlacesService = async (query) => {
      expect(query).toMatchObject({
        q: "coffee",
        lat: 44.43,
        lng: 26.1,
        city: "Bucharest",
        country: "RO",
        limit: 20
      });

      return {
        query: query.q ?? "",
        places: [searchPlace()]
      };
    };
    const app = await buildApp({
      searchPlacesService: service
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      query: "coffee",
      places: [searchPlace()]
    });
  });

  it("enriches saved state for authenticated search requests", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async getSavedPlaceIds(userId, placeIds) {
          expect(userId).toBe(authenticatedUser.id);
          expect(placeIds).toEqual([1, 2]);
          return new Set([2]);
        }
      }),
      searchPlacesService: async (query) => ({
        query: query.q ?? "",
        places: [
          searchPlace({
            id: 1,
            name: "First Coffee"
          }),
          searchPlace({
            id: 2,
            name: "Second Coffee"
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
        isSaved: false
      },
      {
        id: 2,
        isSaved: true
      }
    ]);
  });

  it("returns 401 when an invalid token is sent to search", async () => {
    const app = await buildApp({
      authService,
      searchPlacesService: async () => ({
        query: "coffee",
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

  it("returns 400 for too-short queries", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.searchPlaces}?q=c`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when neither q nor category is sent", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.searchPlaces}?limit=5`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 for an unknown category value", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.searchPlaces}?category=nightlife`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("passes browse-mode category and radius through to the service", async () => {
    const service: SearchPlacesService = async (query) => {
      expect(query.q).toBeUndefined();
      expect(query.category).toEqual(["cafe", "bar"]);
      expect(query.radiusMeters).toBe(1500);
      expect(query.lat).toBe(44.43);

      return {
        query: "",
        places: [searchPlace({ matchReason: "category" })]
      };
    };
    const app = await buildApp({
      searchPlacesService: service
    });

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.searchPlaces}?category=cafe,bar&radiusMeters=1500&lat=44.43&lng=26.10`
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().places[0].matchReason).toBe("category");
  });

  it("returns 400 when only one coordinate is sent", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.searchPlaces}?q=coffee&lat=44.43`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("does not expose unversioned search routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${AppRoute.SearchPlaces}?q=coffee`
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
