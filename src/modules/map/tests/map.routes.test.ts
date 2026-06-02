import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type {
  MapPlacePin,
  MapPlacesResult,
  MapPlacesService
} from "../index.js";

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
    name: "Seneca Anticafe",
    category: "cafe",
    primaryType: "cafe",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    priceLevel: null,
    mapVisibilityScore: 89,
    primaryPhoto: null,
    isSaved: false,
    displayKind: "featured",
    displayPriority: 1,
    ...overrides
  };
}

function mapResult(
  places: MapPlacePin[],
  overrides: Partial<MapPlacesResult["meta"]> = {}
): MapPlacesResult {
  return {
    places,
    meta: {
      returnedCount: places.length,
      limit: 400,
      requestedLimit: null,
      candidateLimit: 400,
      capped: false,
      effectiveZoom: 13,
      minScore: 76,
      featuredMinScore: 92,
      safetyCap: 400,
      capHit: false,
      queryBounds: {
        swLat: 52.48,
        swLng: 13.33,
        neLat: 52.56,
        neLng: 13.47
      },
      ...overrides
    }
  };
}

describe("map routes", () => {
  it("returns map places for a valid bbox query", async () => {
    const app = await buildApp({
      mapPlacesService: async () =>
        mapResult([
          mapPlace({
            name: "Pane e Vino",
            latitude: 52.552578,
            longitude: 13.352883,
            rating: 4
          })
        ])
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
          latitude: 52.552578,
          longitude: 13.352883,
          rating: 4
        })
      ],
      meta: {
        returnedCount: 1,
        limit: 400,
        requestedLimit: null,
        candidateLimit: 400,
        capped: false,
        effectiveZoom: 13,
        minScore: 76,
        featuredMinScore: 92,
        safetyCap: 400,
        capHit: false,
        queryBounds: {
          swLat: 52.48,
          swLng: 13.33,
          neLat: 52.56,
          neLng: 13.47
        }
      }
    });
  });

  it("returns saved state for authenticated map requests", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async getSavedPlaceIds(userId: string, placeIds: number[]) {
          expect(userId).toBe(authenticatedUser.id);
          expect(placeIds).toEqual([1, 2]);
          return new Set([2]);
        }
      }),
      mapPlacesService: async () =>
        mapResult(
          [
            mapPlace({
              id: 1,
              name: "First Coffee",
              latitude: 44.43,
              longitude: 26.09,
              rating: null
            }),
            mapPlace({
              id: 2,
              name: "Second Coffee",
              latitude: 44.44,
              longitude: 26.1,
              rating: null,
              displayKind: "dot",
              displayPriority: 2
            })
          ],
          { capped: true }
        )
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
        displayKind: "featured",
        displayPriority: 1
      },
      {
        id: 2,
        isSaved: true,
        displayKind: "dot",
        displayPriority: 2
      }
    ]);
    expect(response.json().meta).toMatchObject({
      returnedCount: 2,
      capped: true
    });
  });

  it("returns 401 when an invalid token is sent to the map endpoint", async () => {
    const app = await buildApp({
      authService,
      mapPlacesService: async () => mapResult([])
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
      return mapResult([]);
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
      mapPlacesService: async () => mapResult([])
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

  it("returns 400 when limit is over 400", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&limit=401`
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
