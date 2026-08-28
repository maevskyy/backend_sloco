import { describe, expect, it } from "vitest";
import {
  CollectionPlacesOrderError,
  createSavedPlacesService,
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  SavedCollectionNotFoundError,
  type SavedCollection,
  type SavedPlaceSummary,
  type SavedPlacesStoreContract
} from "../index.js";

const userId = "0f70a78a-05f8-45da-81b5-a435fdadf16c";
const collectionId = "4b572b66-d74d-49bb-b9b5-9780c266c6f7";
const savedAt = "2026-05-31T10:00:00.000Z";

const collectionRow = {
  id: collectionId,
  user_id: userId,
  name: "Saved",
  color_hex: "#f0805f",
  slug: "saved",
  is_default: true,
  sort_order: 0,
  created_at: savedAt,
  updated_at: savedAt
};

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

function createRepository(
  overrides: Partial<SavedPlacesStoreContract> = {}
): SavedPlacesStoreContract {
  return {
    async placeExists() {
      return true;
    },
    async ensureDefaultCollection() {
      return collectionRow;
    },
    async ensureSystemCollections() {
      return new Map([["saved", collectionRow]]);
    },
    async listPlaceCollectionIds() {
      return [collectionId];
    },
    async removePlaceFromCollections() {},
    async listCollections() {
      return [collectionRow];
    },
    async getCollectionsByIds() {
      return [collectionRow];
    },
    async getCollection() {
      return collectionRow;
    },
    async createCollection() {
      return collectionRow;
    },
    async updateCollection() {
      return collectionRow;
    },
    async deleteCollection() {},
    async savePlace() {
      return savedAt;
    },
    async unsavePlace() {},
    async addPlaceToCollections() {},
    async removePlaceFromCollection() {},
    async listSavedPlaces() {
      return [placeSummary];
    },
    async listSavedPlaceIds() {
      return [placeSummary.id];
    },
    async countSavedPlaces() {
      return 1;
    },
    async listCollectionPlaces() {
      return [placeSummary];
    },
    async reorderCollectionPlaces() {},
    async getSavedPlaceStates() {
      return new Map();
    },
    ...overrides
  };
}

describe("saved places service", () => {
  it("builds saved dashboard with collections and recently saved places", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(service.getSavedDashboard(userId)).resolves.toMatchObject({
      summary: {
        savedPlaceCount: 1,
        collectionCount: 1,
        recommendationsUseSavedPlaces: true
      },
      collections: [
        {
          id: collectionId,
          placeCount: 1,
          placeIds: [123],
          previewPlaces: [
            {
              id: 123
            }
          ]
        }
      ],
      recentlySaved: [
        {
          id: 123
        }
      ]
    });
  });

  it("returns collection detail with available collections", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.getCollectionDetail(userId, collectionId)
    ).resolves.toMatchObject({
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
          id: collectionId,
          placeCount: 1
        }
      ]
    });
  });

  it("saves an existing place into the default collection", async () => {
    let capturedCollectionIds: string[] = [];
    const service = createSavedPlacesService(
      createRepository({
        async addPlaceToCollections(_userId, _placeId, collectionIds) {
          capturedCollectionIds = collectionIds;
        }
      })
    );

    await expect(service.savePlace(userId, { placeId: 123 })).resolves.toEqual({
      placeId: 123,
      isSaved: true,
      collectionIds: [collectionId],
      savedAt
    });

    expect(capturedCollectionIds).toEqual([collectionId]);
  });

  it("throws when saving a missing place", async () => {
    const service = createSavedPlacesService(
      createRepository({
        async placeExists() {
          return false;
        }
      })
    );

    await expect(service.savePlace(userId, { placeId: 123 })).rejects.toBeInstanceOf(
      PlaceNotFoundError
    );
  });

  it("throws when saving into another user's collection", async () => {
    const service = createSavedPlacesService(
      createRepository({
        async getCollectionsByIds() {
          return [];
        }
      })
    );

    await expect(
      service.savePlace(userId, {
        placeId: 123,
        collectionIds: [collectionId]
      })
    ).rejects.toBeInstanceOf(SavedCollectionNotFoundError);
  });

  it("unsaves an existing place idempotently", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(service.unsavePlace(userId, 123)).resolves.toEqual({
      placeId: 123,
      isSaved: false,
      collectionIds: []
    });
  });

  it("creates a collection", async () => {
    const service = createSavedPlacesService(createRepository());

    const result = await service.createCollection(userId, {
      name: "Coffee & work",
      colorHex: "#e6b15c"
    });

    expect(result.collection).toMatchObject<Partial<SavedCollection>>({
      id: collectionId,
      name: "Saved"
    });
  });

  it("rejects deleting default collection", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.deleteCollection(userId, collectionId)
    ).rejects.toBeInstanceOf(DefaultSavedCollectionDeleteError);
  });

  it("adds a place to a collection", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.addPlaceToCollection(userId, collectionId, 123)
    ).resolves.toEqual({
      placeId: 123,
      isSaved: true,
      collectionIds: [collectionId],
      savedAt
    });
  });

  it("removes a place from a collection without unsaving globally", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.removePlaceFromCollection(userId, collectionId, 123)
    ).resolves.toEqual({
      collectionId,
      placeId: 123,
      removed: true
    });
  });

  it("reorders places in a collection", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.reorderCollectionPlaces(userId, collectionId, [123])
    ).resolves.toEqual({
      collectionId,
      placeIds: [123]
    });
  });

  it("rejects invalid collection place order", async () => {
    const service = createSavedPlacesService(createRepository());

    await expect(
      service.reorderCollectionPlaces(userId, collectionId, [999])
    ).rejects.toBeInstanceOf(CollectionPlacesOrderError);
  });

  it("rejects duplicate collection place order ids", async () => {
    const service = createSavedPlacesService(
      createRepository({
        async listCollectionPlaces() {
          return [
            placeSummary,
            {
              ...placeSummary,
              id: 456
            }
          ];
        }
      })
    );

    await expect(
      service.reorderCollectionPlaces(userId, collectionId, [123, 123])
    ).rejects.toBeInstanceOf(CollectionPlacesOrderError);
  });
});
