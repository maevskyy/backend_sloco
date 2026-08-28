import {
  mapCollectionRow,
  stripPreviewPlaces
} from "../common/saved-places.mappers.js";
import {
  CollectionPlacesOrderError,
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  SavedCollectionNotFoundError
} from "../common/saved-places.errors.js";
import type {
  SavedCollectionRow,
  SavedPlaceSummary,
  SavedPlacesServiceContract,
  SavedPlacesStoreContract
} from "../common/saved-places.types.js";
import { SavedPlacesStore } from "../stores/saved-places.store.js";

const RECENTLY_SAVED_LIMIT = 20;

export class SavedPlacesServiceImpl implements SavedPlacesServiceContract {
  constructor(private readonly store: SavedPlacesStoreContract) {}

  async getSavedDashboard(userId: string) {
    // Creates Saved / Favorites / Been there on a fresh account (TASKS_54).
    await this.store.ensureSystemCollections(userId);

    const [collections, recentlySaved, savedPlaceCount] = await Promise.all([
      this.store.listCollections(userId),
      this.store.listSavedPlaces(userId, RECENTLY_SAVED_LIMIT),
      this.store.countSavedPlaces(userId)
    ]);

    return {
      summary: {
        savedPlaceCount,
        collectionCount: collections.length,
        recommendationsUseSavedPlaces: true as const
      },
      collections: await this.buildCollections(userId, collections),
      recentlySaved
    };
  }

  async getCollectionDetail(userId: string, collectionId: string) {
    const collection = await this.assertCollectionExists(userId, collectionId);
    const [places, collections] = await Promise.all([
      this.store.listCollectionPlaces(userId, collectionId),
      this.store.listCollections(userId)
    ]);
    const collectionDetails = await this.buildCollections(userId, collections);
    const currentCollection =
      collectionDetails.find((item) => item.id === collectionId) ??
      mapCollectionRow(collection, places);

    return {
      collection: stripPreviewPlaces(currentCollection),
      places,
      availableCollections: collectionDetails.map((item) => ({
        id: item.id,
        name: item.name,
        colorHex: item.colorHex,
        placeCount: item.placeCount
      }))
    };
  }

  async savePlace(
    userId: string,
    input: { placeId: number; collectionIds?: string[] }
  ) {
    await this.assertPlaceExists(input.placeId);
    const collectionIds = await this.resolveCollectionIds(userId, input);
    const savedAt = await this.store.savePlace(userId, input.placeId);
    await this.store.addPlaceToCollections(userId, input.placeId, collectionIds);

    return { placeId: input.placeId, isSaved: true as const, collectionIds, savedAt };
  }

  /**
   * The save picker's write (TASKS_54): make the place's list membership EXACTLY
   * `collectionIds`. An empty list means "not saved at all" — the place leaves
   * every list and `saved_places`, which is what an emptied picker means to a user.
   *
   * Membership is diffed rather than wiped and re-added, so `created_at` (and with
   * it the "recently saved" order) survives for lists the user did not touch.
   */
  async setPlaceCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    await this.assertPlaceExists(placeId);

    const wanted = [...new Set(collectionIds)];

    if (wanted.length === 0) {
      return this.unsavePlace(userId, placeId);
    }

    const found = await this.store.getCollectionsByIds(userId, wanted);
    const foundIds = new Set(found.map((collection) => collection.id));
    const missingId = wanted.find((id) => !foundIds.has(id));

    if (missingId) throw new SavedCollectionNotFoundError(missingId);

    const current = await this.store.listPlaceCollectionIds(userId, placeId);
    const currentSet = new Set(current);
    const toAdd = wanted.filter((id) => !currentSet.has(id));
    const toRemove = current.filter((id) => !wanted.includes(id));

    const savedAt = await this.store.savePlace(userId, placeId);
    await this.store.addPlaceToCollections(userId, placeId, toAdd);
    await this.store.removePlaceFromCollections(userId, placeId, toRemove);

    return {
      placeId,
      isSaved: true as const,
      collectionIds: wanted,
      savedAt
    };
  }

  async unsavePlace(userId: string, placeId: number) {
    await this.assertPlaceExists(placeId);
    // Leaving `saved_places` must also leave every list, or the place would keep
    // showing up in Favorites / Been there while the bookmark reads "not saved".
    const memberships = await this.store.listPlaceCollectionIds(userId, placeId);
    await this.store.removePlaceFromCollections(userId, placeId, memberships);
    await this.store.unsavePlace(userId, placeId);

    return { placeId, isSaved: false as const, collectionIds: [] as [] };
  }

  async createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ) {
    return {
      collection: mapCollectionRow(await this.store.createCollection(userId, input), [])
    };
  }

  async updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) {
    const row = await this.store.updateCollection(userId, collectionId, input);

    if (!row) {
      throw new SavedCollectionNotFoundError(collectionId);
    }

    return {
      collection: mapCollectionRow(
        row,
        await this.store.listCollectionPlaces(userId, collectionId)
      )
    };
  }

  async deleteCollection(userId: string, collectionId: string) {
    const collection = await this.assertCollectionExists(userId, collectionId);

    // System lists (Saved / Favorites / Been there) belong to the app, not the user.
    if (collection.is_default || collection.slug !== null) {
      throw new DefaultSavedCollectionDeleteError(collectionId);
    }

    await this.store.deleteCollection(userId, collectionId);

    return { collectionId, deleted: true as const };
  }

  async addPlaceToCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ) {
    await this.assertCollectionExists(userId, collectionId);
    await this.assertPlaceExists(placeId);
    const savedAt = await this.store.savePlace(userId, placeId);
    await this.store.addPlaceToCollections(userId, placeId, [collectionId]);

    return {
      placeId,
      isSaved: true as const,
      collectionIds: [collectionId],
      savedAt
    };
  }

  async removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ) {
    await this.assertCollectionExists(userId, collectionId);
    await this.store.removePlaceFromCollection(userId, collectionId, placeId);

    return { collectionId, placeId, removed: true as const };
  }

  async reorderCollectionPlaces(
    userId: string,
    collectionId: string,
    placeIds: number[]
  ) {
    await this.assertCollectionExists(userId, collectionId);
    const existingPlaceIds = new Set(
      (await this.store.listCollectionPlaces(userId, collectionId)).map(
        (place) => place.id
      )
    );

    if (!isCompleteReorder(placeIds, existingPlaceIds)) {
      throw new CollectionPlacesOrderError(collectionId);
    }

    await this.store.reorderCollectionPlaces(userId, collectionId, placeIds);

    return { collectionId, placeIds };
  }

  async getSavedPlaceIds(userId: string, placeIds: number[]) {
    const states = await this.store.getSavedPlaceStates(userId, placeIds);

    return new Set(
      [...states.entries()]
        .filter(([, state]) => state.isSaved)
        .map(([placeId]) => placeId)
    );
  }

  async listSavedPlaceIds(userId: string) {
    return this.store.listSavedPlaceIds(userId);
  }

  async getSavedPlaceStates(userId: string, placeIds: number[]) {
    return this.store.getSavedPlaceStates(userId, placeIds);
  }

  private async assertPlaceExists(placeId: number) {
    if (!(await this.store.placeExists(placeId))) {
      throw new PlaceNotFoundError(placeId);
    }
  }

  private async assertCollectionExists(userId: string, collectionId: string) {
    const collection = await this.store.getCollection(userId, collectionId);

    if (!collection) {
      throw new SavedCollectionNotFoundError(collectionId);
    }

    return collection;
  }

  private async resolveCollectionIds(
    userId: string,
    input: { placeId: number; collectionIds?: string[] }
  ) {
    const collectionIds = [...new Set(input.collectionIds ?? [])];

    if (collectionIds.length === 0) {
      return [(await this.store.ensureDefaultCollection(userId)).id];
    }

    const foundIds = new Set(
      (await this.store.getCollectionsByIds(userId, collectionIds)).map(
        (collection) => collection.id
      )
    );
    const missingId = collectionIds.find((id) => !foundIds.has(id));

    if (missingId) throw new SavedCollectionNotFoundError(missingId);

    return collectionIds;
  }

  private async buildCollections(
    userId: string,
    collectionRows: SavedCollectionRow[]
  ) {
    const placesByCollectionId = new Map<string, SavedPlaceSummary[]>();

    await Promise.all(
      collectionRows.map(async (collection) => {
        placesByCollectionId.set(
          collection.id,
          await this.store.listCollectionPlaces(userId, collection.id)
        );
      })
    );

    return collectionRows.map((collection) =>
      mapCollectionRow(collection, placesByCollectionId.get(collection.id) ?? [])
    );
  }
}

function isCompleteReorder(placeIds: number[], existingPlaceIds: Set<number>) {
  return (
    placeIds.length === existingPlaceIds.size &&
    new Set(placeIds).size === placeIds.length &&
    placeIds.every((placeId) => existingPlaceIds.has(placeId))
  );
}

export function createSavedPlacesService(
  store: SavedPlacesStoreContract = new SavedPlacesStore()
) {
  return new SavedPlacesServiceImpl(store);
}

export const savedPlacesService = createSavedPlacesService();
