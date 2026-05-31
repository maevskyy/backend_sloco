import { getSupabaseClient } from "../../../lib/supabase.js";
import {
  mapCollectionPlaceRow,
  mapSavedPlaceRow
} from "../common/saved-places.mappers.js";
import type {
  SavedCollectionPlaceRow,
  SavedCollectionRow,
  SavedPlaceRow,
  SavedPlaceState,
  SavedPlacesStoreContract
} from "../common/saved-places.types.js";

const DEFAULT_COLLECTION_NAME = "Want to go";
const DEFAULT_COLLECTION_COLOR = "#f0805f";

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

export class SavedPlacesStore implements SavedPlacesStoreContract {
  async placeExists(placeId: number) {
    const { data, error } = await getSupabaseClient()
      .from("places")
      .select("id")
      .eq("id", placeId)
      .maybeSingle();

    if (error) throw error;

    return data !== null;
  }

  async ensureDefaultCollection(userId: string) {
    const { data: existing, error: existingError } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .eq("is_default", true)
      .maybeSingle();

    if (existingError) throw existingError;
    if (existing) return existing as SavedCollectionRow;

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

    if (!error) return data as SavedCollectionRow;
    if (!hasPostgresErrorCode(error, "23505")) throw error;

    const { data: defaultCollection, error: refetchError } =
      await getSupabaseClient()
        .from("saved_collections")
        .select("*")
        .eq("user_id", userId)
        .eq("is_default", true)
        .single();

    if (refetchError) throw refetchError;

    return defaultCollection as SavedCollectionRow;
  }

  async listCollections(userId: string) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .order("sort_order", { ascending: true })
      .order("created_at", { ascending: true });

    if (error) throw error;

    return (data ?? []) as SavedCollectionRow[];
  }

  async getCollectionsByIds(userId: string, collectionIds: string[]) {
    if (collectionIds.length === 0) return [];

    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .in("id", collectionIds);

    if (error) throw error;

    return (data ?? []) as SavedCollectionRow[];
  }

  async getCollection(userId: string, collectionId: string) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .select("*")
      .eq("user_id", userId)
      .eq("id", collectionId)
      .maybeSingle();

    if (error) throw error;

    return (data as SavedCollectionRow | null) ?? null;
  }

  async createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ) {
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

    if (error) throw error;

    return data as SavedCollectionRow;
  }

  async updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) {
    const update = buildCollectionUpdate(input);
    const { data, error } = await getSupabaseClient()
      .from("saved_collections")
      .update(update)
      .eq("user_id", userId)
      .eq("id", collectionId)
      .select("*")
      .maybeSingle();

    if (error) throw error;

    return (data as SavedCollectionRow | null) ?? null;
  }

  async deleteCollection(userId: string, collectionId: string) {
    const { error } = await getSupabaseClient()
      .from("saved_collections")
      .delete()
      .eq("user_id", userId)
      .eq("id", collectionId);

    if (error) throw error;
  }

  async savePlace(userId: string, placeId: number) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .upsert(
        { user_id: userId, place_id: placeId },
        { onConflict: "user_id,place_id", ignoreDuplicates: true }
      )
      .select("created_at")
      .maybeSingle();

    if (error) throw error;
    if (data?.created_at) return data.created_at as string;

    return this.getSavedAt(userId, placeId);
  }

  async unsavePlace(userId: string, placeId: number) {
    const client = getSupabaseClient();
    const { error: membershipsError } = await client
      .from("saved_collection_places")
      .delete()
      .eq("user_id", userId)
      .eq("place_id", placeId);

    if (membershipsError) throw membershipsError;

    const { error } = await client
      .from("saved_places")
      .delete()
      .eq("user_id", userId)
      .eq("place_id", placeId);

    if (error) throw error;
  }

  async addPlaceToCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    if (collectionIds.length === 0) return;

    const { error } = await getSupabaseClient()
      .from("saved_collection_places")
      .upsert(
        collectionIds.map((collectionId) => ({
          collection_id: collectionId,
          user_id: userId,
          place_id: placeId
        })),
        { onConflict: "collection_id,place_id", ignoreDuplicates: true }
      );

    if (error) throw error;
  }

  async removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ) {
    const { error } = await getSupabaseClient()
      .from("saved_collection_places")
      .delete()
      .eq("user_id", userId)
      .eq("collection_id", collectionId)
      .eq("place_id", placeId);

    if (error) throw error;
  }

  async listSavedPlaces(userId: string, limit: number) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .select(SAVED_PLACE_COLUMNS)
      .eq("user_id", userId)
      .order("created_at", { ascending: false })
      .limit(limit);

    if (error) throw error;

    return ((data ?? []) as unknown as SavedPlaceRow[]).map(mapSavedPlaceRow);
  }

  async countSavedPlaces(userId: string) {
    const { count, error } = await getSupabaseClient()
      .from("saved_places")
      .select("*", { count: "exact", head: true })
      .eq("user_id", userId);

    if (error) throw error;

    return count ?? 0;
  }

  async listCollectionPlaces(userId: string, collectionId: string) {
    const { data, error } = await getSupabaseClient()
      .from("saved_collection_places")
      .select(COLLECTION_PLACE_COLUMNS)
      .eq("user_id", userId)
      .eq("collection_id", collectionId)
      .order("sort_order", { ascending: true })
      .order("created_at", { ascending: true });

    if (error) throw error;

    return ((data ?? []) as unknown as SavedCollectionPlaceRow[]).map(
      mapCollectionPlaceRow
    );
  }

  async reorderCollectionPlaces(
    userId: string,
    collectionId: string,
    placeIds: number[]
  ) {
    await Promise.all(
      placeIds.map(async (placeId, sortOrder) => {
        const { error } = await getSupabaseClient()
          .from("saved_collection_places")
          .update({ sort_order: sortOrder })
          .eq("user_id", userId)
          .eq("collection_id", collectionId)
          .eq("place_id", placeId);

        if (error) throw error;
      })
    );
  }

  async getSavedPlaceStates(userId: string, placeIds: number[]) {
    if (placeIds.length === 0) return new Map<number, SavedPlaceState>();

    const states = await this.getSavedPlaceRows(userId, placeIds);
    const client = getSupabaseClient();
    const { data, error } = await client
      .from("saved_collection_places")
      .select("place_id, collection_id")
      .eq("user_id", userId)
      .in("place_id", placeIds);

    if (error) throw error;

    for (const row of (data ?? []) as unknown as SavedMembershipRow[]) {
      const existing =
        states.get(row.place_id) ?? ({ isSaved: false, collectionIds: [] });

      existing.collectionIds.push(row.collection_id);
      states.set(row.place_id, existing);
    }

    return states;
  }

  private async getSavedAt(userId: string, placeId: number) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .select("created_at")
      .eq("user_id", userId)
      .eq("place_id", placeId)
      .single();

    if (error) throw error;

    return data.created_at as string;
  }

  private async getSavedPlaceRows(userId: string, placeIds: number[]) {
    const { data, error } = await getSupabaseClient()
      .from("saved_places")
      .select("place_id")
      .eq("user_id", userId)
      .in("place_id", placeIds);

    if (error) throw error;

    return new Map<number, SavedPlaceState>(
      ((data ?? []) as unknown as { place_id: number }[]).map((row) => [
        row.place_id,
        { isSaved: true, collectionIds: [] }
      ])
    );
  }
}

type SavedMembershipRow = {
  place_id: number;
  collection_id: string;
};

function buildCollectionUpdate(input: {
  name?: string;
  colorHex?: string | null;
  sortOrder?: number;
}) {
  const update: Record<string, unknown> = {
    updated_at: new Date().toISOString()
  };

  if (input.name !== undefined) update.name = input.name;
  if (input.colorHex !== undefined) update.color_hex = input.colorHex;
  if (input.sortOrder !== undefined) update.sort_order = input.sortOrder;

  return update;
}

function hasPostgresErrorCode(error: unknown, code: string) {
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    (error as { code?: unknown }).code === code
  );
}
