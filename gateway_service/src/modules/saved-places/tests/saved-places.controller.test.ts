import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import {
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  SavedCollectionNotFoundError,
  type SavedCollection,
  type SavedPlaceSummary,
  type SavedPlacesService
} from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const collectionId = "4b572b66-d74d-49bb-b9b5-9780c266c6f7";

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

const savedAt = "2026-05-31T10:00:00.000Z";

const placeSummary: SavedPlaceSummary = {
  id: 123,
  source: "tripadvisor",
  sourceId: "d5529357",
  name: "Quiet Coffee",
  country: "Germany",
  city: "Berlin",
  latitude: 52.52,
  longitude: 13.405,
  category: "cafe",
  categoryLabel: "Cafe",
  rating: 4.5,
  priceLevel: 2,
  tags: ["Coffee", "Work"],
  distanceText: null,
  imageUrl: null,
  savedAt,
  lastViewedAt: null
};

const collection: SavedCollection = {
  id: collectionId,
  name: "Want to go",
  colorHex: "#f0805f",
  placeCount: 1,
  placeIds: [123],
  previewPlaces: [placeSummary],
  createdAt: savedAt,
  updatedAt: savedAt,
  isDefault: true,
  sortOrder: 0
};

function createSavedPlacesService(
  overrides: Partial<SavedPlacesService> = {}
): SavedPlacesService {
  return {
    async getSavedDashboard() {
      return {
        summary: {
          savedPlaceCount: 1,
          collectionCount: 1,
          recommendationsUseSavedPlaces: true
        },
        collections: [collection],
        recentlySaved: [placeSummary]
      };
    },
    async getCollectionDetail() {
      return {
        collection: {
          id: collection.id,
          name: collection.name,
          colorHex: collection.colorHex,
          placeCount: collection.placeCount,
          placeIds: collection.placeIds,
          createdAt: collection.createdAt,
          updatedAt: collection.updatedAt,
          isDefault: collection.isDefault,
          sortOrder: collection.sortOrder
        },
        places: [placeSummary],
        availableCollections: [
          {
            id: collection.id,
            name: collection.name,
            colorHex: collection.colorHex,
            placeCount: collection.placeCount
          }
        ]
      };
    },
    async savePlace(_userId, input) {
      return {
        placeId: input.placeId,
        isSaved: true,
        collectionIds: input.collectionIds ?? [collectionId],
        savedAt
      };
    },
    async unsavePlace(_userId, placeId) {
      return {
        placeId,
        isSaved: false,
        collectionIds: []
      };
    },
    async createCollection() {
      return {
        collection
      };
    },
    async updateCollection() {
      return {
        collection
      };
    },
    async deleteCollection() {
      return {
        collectionId,
        deleted: true
      };
    },
    async addPlaceToCollection(_userId, targetCollectionId, placeId) {
      return {
        placeId,
        isSaved: true,
        collectionIds: [targetCollectionId],
        savedAt
      };
    },
    async removePlaceFromCollection(_userId, targetCollectionId, placeId) {
      return {
        collectionId: targetCollectionId,
        placeId,
        removed: true
      };
    },
    async reorderCollectionPlaces(_userId, targetCollectionId, placeIds) {
      return {
        collectionId: targetCollectionId,
        placeIds
      };
    },
    async getSavedPlaceIds() {
      return new Set<number>();
    },
    async getSavedPlaceStates() {
      return new Map();
    },
    ...overrides
  };
}

describe("saved places routes", () => {
  it("returns 401 when getting saved dashboard without auth", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSaved
    });

    await app.close();

    expect(response.statusCode).toBe(401);
  });

  it("returns saved dashboard", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSaved,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      summary: {
        savedPlaceCount: 1,
        collectionCount: 1,
        recommendationsUseSavedPlaces: true
      },
      collections: [
        {
          id: collectionId,
          placeCount: 1,
          placeIds: [123]
        }
      ],
      recentlySaved: [
        {
          id: 123,
          category: "cafe"
        }
      ]
    });
  });

  it("returns collection detail", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSavedCollection.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      collection: {
        id: collectionId,
        placeIds: [123]
      },
      places: [
        {
          id: 123
        }
      ],
      availableCollections: [
        {
          id: collectionId
        }
      ]
    });
  });

  it("saves a place with optional collection ids", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async savePlace(userId, input) {
          expect(userId).toBe(authenticatedUser.id);
          expect(input).toEqual({
            placeId: 123,
            collectionIds: [collectionId]
          });

          return {
            placeId: input.placeId,
            isSaved: true,
            collectionIds: input.collectionIds ?? [],
            savedAt
          };
        }
      })
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.meSavedPlaces,
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        placeId: 123,
        collectionIds: [collectionId]
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      placeId: 123,
      isSaved: true,
      collectionIds: [collectionId],
      savedAt
    });
  });

  it("returns 404 when saving a missing place", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async savePlace(_userId, input) {
          throw new PlaceNotFoundError(input.placeId);
        }
      })
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.meSavedPlaces,
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        placeId: 999
      }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({
      status: "error",
      message: "Place not found"
    });
  });

  it("unsaves a place idempotently", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.meSavedPlace.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      placeId: 123,
      isSaved: false,
      collectionIds: []
    });
  });

  it("creates a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.meSavedCollections,
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        name: "Coffee & work",
        colorHex: "#e6b15c"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      collection: {
        id: collectionId
      }
    });
  });

  it("updates a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "PATCH",
      url: VersionedAppRoute.meSavedCollection.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        name: "Quiet evenings"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      collection: {
        id: collectionId
      }
    });
  });

  it("deletes a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.meSavedCollection.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      collectionId,
      deleted: true
    });
  });

  it("rejects deleting the default collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async deleteCollection() {
          throw new DefaultSavedCollectionDeleteError(collectionId);
        }
      })
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.meSavedCollection.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(409);
  });

  it("adds a place to a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.meSavedCollectionPlaces.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        placeId: 123
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      placeId: 123,
      isSaved: true,
      collectionIds: [collectionId]
    });
  });

  it("removes a place from a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.meSavedCollectionPlace
        .replace(":collectionId", collectionId)
        .replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      collectionId,
      placeId: 123,
      removed: true
    });
  });

  it("reorders places in a collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "PATCH",
      url: VersionedAppRoute.meSavedCollectionPlacesOrder.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        placeIds: [123]
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      collectionId,
      placeIds: [123]
    });
  });

  it("returns 404 for another user's collection", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService({
        async getCollectionDetail() {
          throw new SavedCollectionNotFoundError(collectionId);
        }
      })
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSavedCollection.replace(
        ":collectionId",
        collectionId
      ),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({
      status: "error",
      message: "Saved collection not found"
    });
  });

  it("returns 400 for invalid place ids", async () => {
    const app = await buildApp({
      authService,
      savedPlacesService: createSavedPlacesService()
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.meSavedPlace.replace(":placeId", "nope"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });
});
