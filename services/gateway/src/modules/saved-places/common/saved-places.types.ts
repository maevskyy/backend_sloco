import type { z } from "zod";
import type {
  deleteCollectionResponseSchema,
  savedCategorySchema,
  savedCollectionCompactSchema,
  savedCollectionDetailResponseSchema,
  savedCollectionSchema,
  savedDashboardResponseSchema,
  savedPlaceSummarySchema,
  savePlaceResponseSchema,
  unsavePlaceResponseSchema
} from "./saved-places.schemas.js";

// HTTP DTO types are inferred from the zod schemas (single source of truth).
export type SavedCategory = z.infer<typeof savedCategorySchema>;

export type PlaceRecord = {
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

export type SavedPlaceRow = {
  created_at: string;
  last_viewed_at: string | null;
  places: PlaceRecord | PlaceRecord[] | null;
};

export type SavedCollectionRow = {
  id: string;
  user_id: string;
  name: string;
  color_hex: string | null;
  is_default: boolean;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type SavedCollectionPlaceRow = {
  collection_id: string;
  place_id: number;
  sort_order: number;
  created_at: string;
  places: PlaceRecord | PlaceRecord[] | null;
};

export type SavedPlaceSummary = z.infer<typeof savedPlaceSummarySchema>;

export type SavedCollection = z.infer<typeof savedCollectionSchema>;

export type SavedCollectionCompact = z.infer<
  typeof savedCollectionCompactSchema
>;

export type SavedDashboardResult = z.infer<
  typeof savedDashboardResponseSchema
>;

export type SavedCollectionDetailResult = z.infer<
  typeof savedCollectionDetailResponseSchema
>;

export type SavePlaceResult = z.infer<typeof savePlaceResponseSchema>;

export type UnsavePlaceResult = z.infer<typeof unsavePlaceResponseSchema>;

export type DeleteCollectionResult = z.infer<
  typeof deleteCollectionResponseSchema
>;

export type SavedPlaceState = {
  isSaved: boolean;
  collectionIds: string[];
};

export type SavedPlacesStoreContract = {
  placeExists(placeId: number): Promise<boolean>;
  ensureDefaultCollection(userId: string): Promise<SavedCollectionRow>;
  listCollections(userId: string): Promise<SavedCollectionRow[]>;
  getCollectionsByIds(
    userId: string,
    collectionIds: string[]
  ): Promise<SavedCollectionRow[]>;
  getCollection(
    userId: string,
    collectionId: string
  ): Promise<SavedCollectionRow | null>;
  createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ): Promise<SavedCollectionRow>;
  updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ): Promise<SavedCollectionRow | null>;
  deleteCollection(userId: string, collectionId: string): Promise<void>;
  savePlace(userId: string, placeId: number): Promise<string>;
  unsavePlace(userId: string, placeId: number): Promise<void>;
  addPlaceToCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ): Promise<void>;
  removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ): Promise<void>;
  listSavedPlaces(userId: string, limit: number): Promise<SavedPlaceSummary[]>;
  listSavedPlaceIds(userId: string): Promise<number[]>;
  countSavedPlaces(userId: string): Promise<number>;
  listCollectionPlaces(
    userId: string,
    collectionId: string
  ): Promise<SavedPlaceSummary[]>;
  reorderCollectionPlaces(
    userId: string,
    collectionId: string,
    placeIds: number[]
  ): Promise<void>;
  getSavedPlaceStates(
    userId: string,
    placeIds: number[]
  ): Promise<Map<number, SavedPlaceState>>;
};

export type SavedPlacesServiceContract = {
  getSavedDashboard(userId: string): Promise<SavedDashboardResult>;
  getCollectionDetail(
    userId: string,
    collectionId: string
  ): Promise<SavedCollectionDetailResult>;
  savePlace(
    userId: string,
    input: { placeId: number; collectionIds?: string[] }
  ): Promise<SavePlaceResult>;
  unsavePlace(userId: string, placeId: number): Promise<UnsavePlaceResult>;
  createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ): Promise<{ collection: SavedCollection }>;
  updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ): Promise<{ collection: SavedCollection }>;
  deleteCollection(
    userId: string,
    collectionId: string
  ): Promise<DeleteCollectionResult>;
  addPlaceToCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ): Promise<SavePlaceResult>;
  removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ): Promise<{ collectionId: string; placeId: number; removed: true }>;
  reorderCollectionPlaces(
    userId: string,
    collectionId: string,
    placeIds: number[]
  ): Promise<{ collectionId: string; placeIds: number[] }>;
  getSavedPlaceIds(userId: string, placeIds: number[]): Promise<Set<number>>;
  listSavedPlaceIds(userId: string): Promise<number[]>;
  getSavedPlaceStates(
    userId: string,
    placeIds: number[]
  ): Promise<Map<number, SavedPlaceState>>;
};
