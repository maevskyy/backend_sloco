import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { MapPlacesService } from "../index.js";

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

describe("map routes", () => {
  it("returns map places for a valid bbox query", async () => {
    const app = await buildApp({
      mapPlacesService: async () => ({
        places: [
          {
            id: 1,
            source: "tripadvisor",
            sourceId: "d5529357",
            name: "Pane e Vino",
            country: "Germany",
            city: "Berlin",
            latitude: 52.552578,
            longitude: 13.352883,
            rating: 4,
            priceLevel: null,
            numberOfReviews: 17,
            rawCuisineStyle: null,
            isSaved: false,
            savedCollectionIds: [],
            displayKind: "featured",
            displayPriority: 1
          }
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
        {
          id: 1,
          source: "tripadvisor",
          sourceId: "d5529357",
          name: "Pane e Vino",
          country: "Germany",
          city: "Berlin",
          latitude: 52.552578,
          longitude: 13.352883,
          rating: 4,
          priceLevel: null,
          numberOfReviews: 17,
          rawCuisineStyle: null,
          isSaved: false,
          savedCollectionIds: [],
          displayKind: "featured",
          displayPriority: 1
        }
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
          {
            id: 1,
            source: "osm",
            sourceId: "osm:node/1",
            name: "First Coffee",
            country: "Romania",
            city: "Bucharest",
            latitude: 44.43,
            longitude: 26.09,
            rating: null,
            priceLevel: null,
            numberOfReviews: null,
            rawCuisineStyle: null,
            isSaved: false,
            savedCollectionIds: [],
            displayKind: "featured",
            displayPriority: 1
          },
          {
            id: 2,
            source: "osm",
            sourceId: "osm:node/2",
            name: "Second Coffee",
            country: "Romania",
            city: "Bucharest",
            latitude: 44.44,
            longitude: 26.1,
            rating: null,
            priceLevel: null,
            numberOfReviews: null,
            rawCuisineStyle: null,
            isSaved: false,
            savedCollectionIds: [],
            displayKind: "dot",
            displayPriority: 2
          }
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
