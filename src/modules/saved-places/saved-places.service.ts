import { getSupabaseClient } from "../../lib/supabase.js";

const DEFAULT_COLLECTION_NAME = "Want to go";
const DEFAULT_COLLECTION_COLOR = "#f0805f";
const RECENTLY_SAVED_LIMIT = 20;

const PLACE_COLUMNS = [
  "id",
  "source",
  "source_id",
  "name",
  "country",
  "city",
  "category",
  "latitude",
  "longitude",
  "rating",
  "price_level",
  "attributes"
].join(",");

const SAVED_PLACE_COLUMNS = [
  "created_at",
  "last_viewed_at",
  `places!inner(${PLACE_COLUMNS})`
].join(",");

const COLLECTION_PLACE_COLUMNS = [
  "collection_id",
  "place_id",
  "sort_order",
  "created_at",
  `places!inner(${PLACE_COLUMNS})`
].join(",");

export class PlaceNotFoundError extends Error {
  constructor(placeId: number) {
    super(`Place ${placeId} not found`);
    this.name = "PlaceNotFoundError";
  }
}

export class SavedCollectionNotFoundError extends Error {
  constructor(collectionId: string) {
    super(`Saved collection ${collectionId} not found`);
    this.name = "SavedCollectionNotFoundError";
  }
}

export class DefaultSavedCollectionDeleteError extends Error {
  constructor(collectionId: string) {
    super(`Default saved collection ${collectionId} cannot be deleted`);
    this.name = "DefaultSavedCollectionDeleteError";
  }
}

export class CollectionPlacesOrderError extends Error {
  constructor(collectionId: string) {
    super(`Invalid place order for collection ${collectionId}`);
    this.name = "CollectionPlacesOrderError";
  }
}

type SavedCategory =
  | "food"
  | "cafe"
  | "bar"
  | "nature"
  | "culture"
  | "music"
  | "other";

type PlaceRecord = {
  id: number;
  source: string;
  source_id: string;
  name: string;
  country: string;
  city: string;
  category: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  attributes: Record<string, unknown> | null;
};

type SavedPlaceRow = {
  created_at: string;
  last_viewed_at: string | null;
  places: PlaceRecord | PlaceRecord[] | null;
};

type SavedCollectionRow = {
  id: string;
  user_id: string;
  name: string;
  color_hex: string | null;
  is_default: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

type SavedCollectionPlaceRow = {
  collection_id: string;
  place_id: number;
  sort_order: number;
  created_at: string;
  places: PlaceRecord | PlaceRecord[] | null;
};

export type SavedPlaceSummary = {
  id: number;
  source: string;
  sourceId: string;
  name: string;
  city: string;
  country: string;
  latitude: number;
  longitude: number;
  category: SavedCategory;
  categoryLabel: string;
  rating: number | null;
  priceLevel: 0 | 1 | 2 | 3 | 4 | null;
  tags: string[];
  distanceText: string | null;
  imageUrl: string | null;
  savedAt: string;
  lastViewedAt: string | null;
};

export type SavedCollection = {
  id: string;
  name: string;
  colorHex: string | null;
  placeCount: number;
  placeIds: number[];
  previewPlaces: SavedPlaceSummary[];
  createdAt: string;
  updatedAt: string;
  isDefault: boolean;
  sortOrder: number;
};

export type SavedCollectionCompact = Pick<
  SavedCollection,
  "id" | "name" | "colorHex" | "placeCount"
>;

export type SavedDashboardResult = {
  summary: {
    savedPlaceCount: number;
    collectionCount: number;
    recommendationsUseSavedPlaces: true;
  };
  collections: SavedCollection[];
  recentlySaved: SavedPlaceSummary[];
};

export type SavedCollectionDetailResult = {
  collection: Omit<SavedCollection, "previewPlaces">;
  places: SavedPlaceSummary[];
  availableCollections: SavedCollectionCompact[];
};

export type SavePlaceResult = {
  placeId: number;
  isSaved: true;
  collectionIds: string[];
  savedAt: string;
};

export type UnsavePlaceResult = {
  placeId: number;
  isSaved: false;
  collectionIds: [];
};

export type DeleteCollectionResult = {
  collectionId: string;
  deleted: true;
};

export type SavedPlaceState = {
  isSaved: boolean;
  collectionIds: string[];
};

export type SavedPlacesRepository = {
  placeExists: (placeId: number) => Promise<boolean>;
  ensureDefaultCollection: (userId: string) => Promise<SavedCollectionRow>;
  listCollections: (userId: string) => Promise<SavedCollectionRow[]>;
  getCollectionsByIds: (
    userId: string,
    collectionIds: string[]
  ) => Promise<SavedCollectionRow[]>;
  getCollection: (
    userId: string,
    collectionId: string
  ) => Promise<SavedCollectionRow | null>;
  createCollection: (
    userId: string,
    input: { name: string; colorHex?: string }
  ) => Promise<SavedCollectionRow>;
  updateCollection: (
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) => Promise<SavedCollectionRow | null>;
  deleteCollection: (userId: string, collectionId: string) => Promise<void>;
  savePlace: (userId: string, placeId: number) => Promise<string>;
  unsavePlace: (userId: string, placeId: number) => Promise<void>;
  addPlaceToCollections: (
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) => Promise<void>;
  removePlaceFromCollection: (
    userId: string,
    collectionId: string,
    placeId: number
  ) => Promise<void>;
  listSavedPlaces: (
    userId: string,
    limit: number
  ) => Promise<SavedPlaceSummary[]>;
  countSavedPlaces: (userId: string) => Promise<number>;
  listCollectionPlaces: (
    userId: string,
    collectionId: string
  ) => Promise<SavedPlaceSummary[]>;
  reorderCollectionPlaces: (
    userId: string,
    collectionId: string,
    placeIds: number[]
  ) => Promise<void>;
  getSavedPlaceStates: (
    userId: string,
    placeIds: number[]
  ) => Promise<Map<number, SavedPlaceState>>;
};

export type SavedPlacesService = {
  getSavedDashboard: (userId: string) => Promise<SavedDashboardResult>;
  getCollectionDetail: (
    userId: string,
    collectionId: string
  ) => Promise<SavedCollectionDetailResult>;
  savePlace: (
    userId: string,
    input: { placeId: number; collectionIds?: string[] }
  ) => Promise<SavePlaceResult>;
  unsavePlace: (userId: string, placeId: number) => Promise<UnsavePlaceResult>;
  createCollection: (
    userId: string,
    input: { name: string; colorHex?: string }
  ) => Promise<{ collection: SavedCollection }>;
  updateCollection: (
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) => Promise<{ collection: SavedCollection }>;
  deleteCollection: (
    userId: string,
    collectionId: string
  ) => Promise<DeleteCollectionResult>;
  addPlaceToCollection: (
    userId: string,
    collectionId: string,
    placeId: number
  ) => Promise<SavePlaceResult>;
  removePlaceFromCollection: (
    userId: string,
    collectionId: string,
    placeId: number
  ) => Promise<{ collectionId: string; placeId: number; removed: true }>;
  reorderCollectionPlaces: (
    userId: string,
    collectionId: string,
    placeIds: number[]
  ) => Promise<{ collectionId: string; placeIds: number[] }>;
  getSavedPlaceIds: (userId: string, placeIds: number[]) => Promise<Set<number>>;
  getSavedPlaceStates: (
    userId: string,
    placeIds: number[]
  ) => Promise<Map<number, SavedPlaceState>>;
};

export const supabaseSavedPlacesRepository: SavedPlacesRepository = {
  async placeExists(placeId) {
    const { data, error } = await getSupabaseClient()
      .from("places")
      .select("id")
      .eq("id", placeId)
      .maybeSingle();

    if (error) {
      throw error;
    }

    return data !== null;
  },

  async ensureDefaultCollection(userId) {
    const { data: existing, error: existingError } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .eq("is_default", true)
      .maybeSingle();

    if (existingError) {
      throw existingError;
    }

    if (existing) {
      return existing as SavedCollectionRow;
    }

    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .insert({
        user_id: userId,
        name: DEFAULT_COLLECTION_NAME,
        color_hex: DEFAULT_COLLECTION_COLOR,
        is_default: true,
        sort_order: 0
      })
      .select("*")
      .single();

    if (error) {
      if (hasPostgresErrorCode(error, "23505")) {
        const { data: defaultCollection, error: refetchError } =
          await getSupabaseClient()
            .from("saved_collections")
            .select("*")
            .eq("user_id", userId)
            .eq("is_default", true)
            .single();

        if (refetchError) {
          throw refetchError;
        }

        return defaultCollection as SavedCollectionRow;
      }

      throw error;
    }

    return data as SavedCollectionRow;
  },

  async listCollections(userId) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .order("sort_order", { ascending: true })
      .order("created_at", { ascending: true });

    if (error) {
      throw error;
    }

    return (data ?? []) as SavedCollectionRow[];
  },

  async getCollectionsByIds(userId, collectionIds) {
    if (collectionIds.length === 0) {
      return [];
    }

    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .in("id", collectionIds);

    if (error) {
      throw error;
    }

    return (data ?? []) as SavedCollectionRow[];
  },

  async getCollection(userId, collectionId) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .eq("id", collectionId)
      .maybeSingle();

    if (error) {
      throw error;
    }

    return (data as SavedCollectionRow | null) ?? null;
  },

  async createCollection(userId, input) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .insert({
        user_id: userId,
        name: input.name,
        color_hex: input.colorHex ?? null,
        is_default: false
      })
      .select("*")
      .single();

    if (error) {
      throw error;
    }

    return data as SavedCollectionRow;
  },

  async updateCollection(userId, collectionId, input) {
    const update: Record<string, unknown> = {
      updated_at: new Date().toISOString()
    };

    if (input.name !== undefined) {
      update.name = input.name;
    }

    if (input.colorHex !== undefined) {
      update.color_hex = input.colorHex;
    }

    if (input.sortOrder !== undefined) {
      update.sort_order = input.sortOrder;
    }

    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .update(update)
      .eq("user_id", userId)
      .eq("id", collectionId)
      .select("*")
      .maybeSingle();

    if (error) {
      throw error;
    }

    return (data as SavedCollectionRow | null) ?? null;
  },

  async deleteCollection(userId, collectionId) {
    const { error } = await getSupabaseClient()
      .from("saved_collections")
      .delete()
      .eq("user_id", userId)
      .eq("id", collectionId);

    if (error) {
      throw error;
    }
  },

  async savePlace(userId, placeId) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .upsert(
        {
          user_id: userId,
          place_id: placeId
        },
        {
          onConflict: "user_id,place_id",
          ignoreDuplicates: true
        }
      )
      .select("created_at")
      .maybeSingle();

    if (error) {
      throw error;
    }

    if (data?.created_at) {
      return data.created_at as string;
    }

    const { data: existing, error: existingError } = await getSupabaseClient()
      .from("saved_places")
      .select("created_at")
      .eq("user_id", userId)
      .eq("place_id", placeId)
      .single();

    if (existingError) {
      throw existingError;
    }

    return existing.created_at as string;
  },

  async unsavePlace(userId, placeId) {
    const client = getSupabaseClient();

    const { error: membershipsError } = await client
      .from("saved_collection_places")
      .delete()
      .eq("user_id", userId)
      .eq("place_id", placeId);

    if (membershipsError) {
      throw membershipsError;
    }

    const { error } = await client
      .from("saved_places")
      .delete()
      .eq("user_id", userId)
      .eq("place_id", placeId);

    if (error) {
      throw error;
    }
  },

  async addPlaceToCollections(userId, placeId, collectionIds) {
    if (collectionIds.length === 0) {
      return;
    }

    const { error } = await getSupabaseClient()
      .from("saved_collection_places")
      .upsert(
        collectionIds.map((collectionId) => ({
          collection_id: collectionId,
          user_id: userId,
          place_id: placeId
        })),
        {
          onConflict: "collection_id,place_id",
          ignoreDuplicates: true
        }
      );

    if (error) {
      throw error;
    }
  },

  async removePlaceFromCollection(userId, collectionId, placeId) {
    const { error } = await getSupabaseClient()
      .from("saved_collection_places")
      .delete()
      .eq("user_id", userId)
      .eq("collection_id", collectionId)
      .eq("place_id", placeId);

    if (error) {
      throw error;
    }
  },

  async listSavedPlaces(userId, limit) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .select(SAVED_PLACE_COLUMNS)
      .eq("user_id", userId)
      .order("created_at", {
        ascending: false
      })
      .limit(limit);

    if (error) {
      throw error;
    }

    return ((data ?? []) as unknown as SavedPlaceRow[]).map(mapSavedPlaceRow);
  },

  async countSavedPlaces(userId) {
    const { count, error } = await getSupabaseClient()
      .from("saved_places")
      .select("*", {
        count: "exact",
        head: true
      })
      .eq("user_id", userId);

    if (error) {
      throw error;
    }

    return count ?? 0;
  },

  async listCollectionPlaces(userId, collectionId) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collection_places")
      .select(COLLECTION_PLACE_COLUMNS)
      .eq("user_id", userId)
      .eq("collection_id", collectionId)
      .order("sort_order", {
        ascending: true
      })
      .order("created_at", {
        ascending: true
      });

    if (error) {
      throw error;
    }

    return ((data ?? []) as unknown as SavedCollectionPlaceRow[]).map(
      mapCollectionPlaceRow
    );
  },

  async reorderCollectionPlaces(userId, collectionId, placeIds) {
    await Promise.all(
      placeIds.map(async (placeId, sortOrder) => {
        const { error } = await getSupabaseClient()
          .from("saved_collection_places")
          .update({
            sort_order: sortOrder
          })
          .eq("user_id", userId)
          .eq("collection_id", collectionId)
          .eq("place_id", placeId);

        if (error) {
          throw error;
        }
      })
    );
  },

  async getSavedPlaceStates(userId, placeIds) {
    if (placeIds.length === 0) {
      return new Map<number, SavedPlaceState>();
    }

    const client = getSupabaseClient();
    const { data: savedRows, error: savedError } = await client
      .from("saved_places")
      .select("place_id")
      .eq("user_id", userId)
      .in("place_id", placeIds);

    if (savedError) {
      throw savedError;
    }

    const states = new Map<number, SavedPlaceState>();

    for (const row of (savedRows ?? []) as unknown as { place_id: number }[]) {
      states.set(row.place_id, {
        isSaved: true,
        collectionIds: []
      });
    }

    const { data: membershipRows, error: membershipError } = await client
      .from("saved_collection_places")
      .select("place_id, collection_id")
      .eq("user_id", userId)
      .in("place_id", placeIds);

    if (membershipError) {
      throw membershipError;
    }

    for (const row of (membershipRows ?? []) as unknown as {
      place_id: number;
      collection_id: string;
    }[]) {
      const existing =
        states.get(row.place_id) ??
        ({
          isSaved: false,
          collectionIds: []
        } satisfies SavedPlaceState);

      existing.collectionIds.push(row.collection_id);
      states.set(row.place_id, existing);
    }

    return states;
  }
};

export function createSavedPlacesService(
  repository: SavedPlacesRepository = supabaseSavedPlacesRepository
): SavedPlacesService {
  return {
    async getSavedDashboard(userId) {
      await repository.ensureDefaultCollection(userId);

      const [collections, recentlySaved, savedPlaceCount] = await Promise.all([
        repository.listCollections(userId),
        repository.listSavedPlaces(userId, RECENTLY_SAVED_LIMIT),
        repository.countSavedPlaces(userId)
      ]);

      const collectionDetails = await buildCollections(repository, userId, collections);

      return {
        summary: {
          savedPlaceCount,
          collectionCount: collections.length,
          recommendationsUseSavedPlaces: true
        },
        collections: collectionDetails,
        recentlySaved
      };
    },

    async getCollectionDetail(userId, collectionId) {
      const collection = await assertCollectionExists(
        repository,
        userId,
        collectionId
      );
      const [places, collections] = await Promise.all([
        repository.listCollectionPlaces(userId, collectionId),
        repository.listCollections(userId)
      ]);
      const collectionDetails = await buildCollections(
        repository,
        userId,
        collections
      );
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
    },

    async savePlace(userId, input) {
      await assertPlaceExists(repository, input.placeId);
      const collectionIds = await resolveCollectionIds(repository, userId, input);
      const savedAt = await repository.savePlace(userId, input.placeId);
      await repository.addPlaceToCollections(userId, input.placeId, collectionIds);

      return {
        placeId: input.placeId,
        isSaved: true,
        collectionIds,
        savedAt
      };
    },

    async unsavePlace(userId, placeId) {
      await assertPlaceExists(repository, placeId);
      await repository.unsavePlace(userId, placeId);

      return {
        placeId,
        isSaved: false,
        collectionIds: []
      };
    },

    async createCollection(userId, input) {
      const row = await repository.createCollection(userId, input);

      return {
        collection: mapCollectionRow(row, [])
      };
    },

    async updateCollection(userId, collectionId, input) {
      const row = await repository.updateCollection(userId, collectionId, input);

      if (!row) {
        throw new SavedCollectionNotFoundError(collectionId);
      }

      const places = await repository.listCollectionPlaces(userId, collectionId);

      return {
        collection: mapCollectionRow(row, places)
      };
    },

    async deleteCollection(userId, collectionId) {
      const collection = await assertCollectionExists(
        repository,
        userId,
        collectionId
      );

      if (collection.is_default) {
        throw new DefaultSavedCollectionDeleteError(collectionId);
      }

      await repository.deleteCollection(userId, collectionId);

      return {
        collectionId,
        deleted: true
      };
    },

    async addPlaceToCollection(userId, collectionId, placeId) {
      await assertCollectionExists(repository, userId, collectionId);
      await assertPlaceExists(repository, placeId);
      const savedAt = await repository.savePlace(userId, placeId);
      await repository.addPlaceToCollections(userId, placeId, [collectionId]);

      return {
        placeId,
        isSaved: true,
        collectionIds: [collectionId],
        savedAt
      };
    },

    async removePlaceFromCollection(userId, collectionId, placeId) {
      await assertCollectionExists(repository, userId, collectionId);
      await repository.removePlaceFromCollection(userId, collectionId, placeId);

      return {
        collectionId,
        placeId,
        removed: true
      };
    },

    async reorderCollectionPlaces(userId, collectionId, placeIds) {
      await assertCollectionExists(repository, userId, collectionId);
      const existingPlaceIds = new Set(
        (await repository.listCollectionPlaces(userId, collectionId)).map(
          (place) => place.id
        )
      );

      if (
        placeIds.length !== existingPlaceIds.size ||
        new Set(placeIds).size !== placeIds.length ||
        placeIds.some((placeId) => !existingPlaceIds.has(placeId))
      ) {
        throw new CollectionPlacesOrderError(collectionId);
      }

      await repository.reorderCollectionPlaces(userId, collectionId, placeIds);

      return {
        collectionId,
        placeIds
      };
    },

    async getSavedPlaceIds(userId, placeIds) {
      const states = await repository.getSavedPlaceStates(userId, placeIds);

      return new Set(
        [...states.entries()]
          .filter(([, state]) => state.isSaved)
          .map(([placeId]) => placeId)
      );
    },

    async getSavedPlaceStates(userId, placeIds) {
      return repository.getSavedPlaceStates(userId, placeIds);
    }
  };
}

export const savedPlacesService = createSavedPlacesService();

async function assertPlaceExists(
  repository: SavedPlacesRepository,
  placeId: number
) {
  if (!(await repository.placeExists(placeId))) {
    throw new PlaceNotFoundError(placeId);
  }
}

async function assertCollectionExists(
  repository: SavedPlacesRepository,
  userId: string,
  collectionId: string
) {
  const collection = await repository.getCollection(userId, collectionId);

  if (!collection) {
    throw new SavedCollectionNotFoundError(collectionId);
  }

  return collection;
}

async function resolveCollectionIds(
  repository: SavedPlacesRepository,
  userId: string,
  input: { placeId: number; collectionIds?: string[] }
) {
  const collectionIds = [...new Set(input.collectionIds ?? [])];

  if (collectionIds.length === 0) {
    return [(await repository.ensureDefaultCollection(userId)).id];
  }

  const collections = await repository.getCollectionsByIds(userId, collectionIds);
  const foundIds = new Set(collections.map((collection) => collection.id));
  const missingId = collectionIds.find((collectionId) => !foundIds.has(collectionId));

  if (missingId) {
    throw new SavedCollectionNotFoundError(missingId);
  }

  return collectionIds;
}

async function buildCollections(
  repository: SavedPlacesRepository,
  userId: string,
  collectionRows: SavedCollectionRow[]
) {
  const placesByCollectionId = new Map<string, SavedPlaceSummary[]>();

  await Promise.all(
    collectionRows.map(async (collection) => {
      placesByCollectionId.set(
        collection.id,
        await repository.listCollectionPlaces(userId, collection.id)
      );
    })
  );

  return collectionRows.map((collection) =>
    mapCollectionRow(collection, placesByCollectionId.get(collection.id) ?? [])
  );
}

function mapCollectionRow(
  row: SavedCollectionRow,
  places: SavedPlaceSummary[]
): SavedCollection {
  return {
    id: row.id,
    name: row.name,
    colorHex: row.color_hex,
    placeCount: places.length,
    placeIds: places.map((place) => place.id),
    previewPlaces: places.slice(0, 3),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    isDefault: row.is_default,
    sortOrder: row.sort_order
  };
}

function stripPreviewPlaces(
  collection: SavedCollection
): Omit<SavedCollection, "previewPlaces"> {
  return {
    id: collection.id,
    name: collection.name,
    colorHex: collection.colorHex,
    placeCount: collection.placeCount,
    placeIds: collection.placeIds,
    createdAt: collection.createdAt,
    updatedAt: collection.updatedAt,
    isDefault: collection.isDefault,
    sortOrder: collection.sortOrder
  };
}

function mapSavedPlaceRow(row: SavedPlaceRow): SavedPlaceSummary {
  return mapPlaceSummary(
    normalizePlaceRecord(row.places),
    row.created_at,
    row.last_viewed_at
  );
}

function mapCollectionPlaceRow(row: SavedCollectionPlaceRow): SavedPlaceSummary {
  return mapPlaceSummary(normalizePlaceRecord(row.places), row.created_at, null);
}

function mapPlaceSummary(
  place: PlaceRecord,
  savedAt: string,
  lastViewedAt: string | null
): SavedPlaceSummary {
  const category = normalizeCategory(place.category, place.attributes);

  return {
    id: place.id,
    source: place.source,
    sourceId: place.source_id,
    name: place.name,
    city: place.city,
    country: place.country,
    latitude: place.latitude,
    longitude: place.longitude,
    category,
    categoryLabel: getCategoryLabel(category),
    rating: place.rating,
    priceLevel: normalizePriceLevel(place.price_level),
    tags: extractTags(place),
    distanceText: null,
    imageUrl: null,
    savedAt,
    lastViewedAt
  };
}

function normalizePlaceRecord(
  place: PlaceRecord | PlaceRecord[] | null
): PlaceRecord {
  if (Array.isArray(place)) {
    const firstPlace = place[0];

    if (firstPlace) {
      return firstPlace;
    }

    throw new Error("Saved place row is missing joined place data");
  }

  if (place) {
    return place;
  }

  throw new Error("Saved place row is missing joined place data");
}

function normalizeCategory(
  category: string,
  attributes: Record<string, unknown> | null
): SavedCategory {
  const searchable = [
    category,
    getStringAttribute(attributes, "raw_cuisine_style"),
    getStringAttribute(attributes, "cuisine"),
    getStringAttribute(attributes, "embedding_text")
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (searchable.match(/coffee|cafe|espresso|brunch/)) {
    return "cafe";
  }

  if (searchable.match(/bar|pub|wine|cocktail|beer/)) {
    return "bar";
  }

  if (searchable.match(/museum|gallery|art|culture|theatre|theater/)) {
    return "culture";
  }

  if (searchable.match(/music|jazz|club|techno|concert/)) {
    return "music";
  }

  if (searchable.match(/park|garden|nature|walk/)) {
    return "nature";
  }

  if (
    searchable.match(
      /restaurant|food|pizza|sushi|asian|italian|german|cuisine|vegan|vegetarian/
    )
  ) {
    return "food";
  }

  return "other";
}

function getCategoryLabel(category: SavedCategory) {
  const labels: Record<SavedCategory, string> = {
    food: "Food",
    cafe: "Cafe",
    bar: "Bar",
    nature: "Nature",
    culture: "Culture",
    music: "Music",
    other: "Other"
  };

  return labels[category];
}

function normalizePriceLevel(priceLevel: number | null): 0 | 1 | 2 | 3 | 4 | null {
  if (priceLevel === null) {
    return null;
  }

  if ([0, 1, 2, 3, 4].includes(priceLevel)) {
    return priceLevel as 0 | 1 | 2 | 3 | 4;
  }

  return null;
}

function extractTags(place: PlaceRecord) {
  const rawCuisineStyle =
    getStringAttribute(place.attributes, "raw_cuisine_style") ??
    getStringAttribute(place.attributes, "cuisine");

  if (!rawCuisineStyle) {
    return [getCategoryLabel(normalizeCategory(place.category, place.attributes))];
  }

  return rawCuisineStyle
    .replaceAll("[", "")
    .replaceAll("]", "")
    .replaceAll("'", "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 3);
}

function getStringAttribute(
  attributes: Record<string, unknown> | null,
  key: string
) {
  const value = attributes?.[key];

  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function hasPostgresErrorCode(error: unknown, code: string) {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: unknown }).code === code
  );
}
