import {
  getSupabaseClient,
  hasPostgresErrorCode
} from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
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

// The three SYSTEM lists every user gets (TASKS_54). They are auto-created, cannot
// be deleted, are hidden from "My lists" by the client and are pinned to the top of
// its save picker. `slug` is their stable identity; `name` is display text.
// `saved` is also the default: a save that names no list lands there.
const SYSTEM_COLLECTIONS = [
  { slug: "saved", name: "Saved", colorHex: "#f0805f", sortOrder: 0, isDefault: true },
  { slug: "favorites", name: "Favorites", colorHex: "#e6b15c", sortOrder: 1, isDefault: false },
  { slug: "been", name: "Been there", colorHex: "#8fb996", sortOrder: 2, isDefault: false }
] as const;

export const SYSTEM_COLLECTION_SLUGS = SYSTEM_COLLECTIONS.map((item) => item.slug);

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

function measureSavedPlacesDependency<T>(
  operation: string,
  name: string,
  callback: () => Promise<T>,
  getRowsCount?: (result: T) => number | undefined
) {
  return measureDependencyMetric(
    {
      dependency: "supabase",
      operation,
      name
    },
    callback,
    getRowsCount
  );
}

export class SavedPlacesStore implements SavedPlacesStoreContract {
  async placeExists(placeId: number) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "places_exists",
      async () =>
        getSupabaseClient()
          .from("places")
          .select("id")
          .eq("id", placeId)
          .maybeSingle()
    );

    if (error) throw error;

    return data !== null;
  }

  /**
   * Create the three system lists for this user if they are missing, and return
   * them by slug. Idempotent, and safe against a user who already created a list
   * under a system NAME: that row is adopted (its slug is filled in) instead of
   * colliding with `unique (user_id, name)`.
   */
  async ensureSystemCollections(userId: string) {
    const existing = await this.listCollections(userId);
    const bySlug = new Map<string, SavedCollectionRow>(
      existing
        .filter((row) => row.slug !== null)
        .map((row) => [row.slug as string, row])
    );

    for (const system of SYSTEM_COLLECTIONS) {
      if (bySlug.has(system.slug)) continue;

      const sameName = existing.find(
        (row) => row.slug === null && row.name === system.name
      );

      if (sameName) {
        const { data, error } = await measureSavedPlacesDependency(
          "update",
          "saved_collections_adopt_system",
          async () =>
            getSupabaseClient()
              .from("saved_collections")
              .update({ slug: system.slug })
              .eq("id", sameName.id)
              .eq("user_id", userId)
              .select("*")
              .single()
        );

        if (error) throw error;
        bySlug.set(system.slug, data as SavedCollectionRow);
        continue;
      }

      const { data, error } = await measureSavedPlacesDependency(
        "insert",
        "saved_collections_system",
        async () =>
          getSupabaseClient()
            .from("saved_collections")
            .insert({
              user_id: userId,
              name: system.name,
              slug: system.slug,
              color_hex: system.colorHex,
              // The default flag has a partial unique index (one per user), so it is
              // only claimed when the user has no default yet.
              is_default:
                system.isDefault && !existing.some((row) => row.is_default),
              sort_order: system.sortOrder
            })
            .select("*")
            .single()
      );

      if (!error) {
        bySlug.set(system.slug, data as SavedCollectionRow);
        continue;
      }

      // Lost a race with a concurrent request: read back what the winner wrote.
      if (!hasPostgresErrorCode(error, "23505")) throw error;

      const { data: raced, error: refetchError } =
        await measureSavedPlacesDependency(
          "select",
          "saved_collections_system_refetch",
          async () =>
            getSupabaseClient()
              .from("saved_collections")
              .select("*")
              .eq("user_id", userId)
              .eq("slug", system.slug)
              .single()
        );

      if (refetchError) throw refetchError;
      bySlug.set(system.slug, raced as SavedCollectionRow);
    }

    return bySlug;
  }

  async ensureDefaultCollection(userId: string) {
    const bySlug = await this.ensureSystemCollections(userId);
    const fallback = bySlug.get("saved");

    if (fallback) return fallback;

    // Only reachable if someone cleared the slug by hand.
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collections_default",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .select("*")
          .eq("user_id", userId)
          .eq("is_default", true)
          .single()
    );

    if (error) throw error;

    return data as SavedCollectionRow;
  }

  /** Membership of one place across the user's lists. */
  async listPlaceCollectionIds(userId: string, placeId: number) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_of_place",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .select("collection_id")
          .eq("user_id", userId)
          .eq("place_id", placeId)
    );

    if (error) throw error;

    return (data ?? []).map((row) => (row as { collection_id: string }).collection_id);
  }

  async removePlaceFromCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    if (collectionIds.length === 0) return;

    const { error } = await measureSavedPlacesDependency(
      "delete",
      "saved_collection_places_remove_many",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .delete()
          .eq("user_id", userId)
          .eq("place_id", placeId)
          .in("collection_id", collectionIds),
      () => collectionIds.length
    );

    if (error) throw error;
  }

  async listCollections(userId: string) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collections_list",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .select("*")
          .eq("user_id", userId)
          .order("sort_order", { ascending: true })
          .order("created_at", { ascending: true }),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as SavedCollectionRow[];
  }

  async getCollectionsByIds(userId: string, collectionIds: string[]) {
    if (collectionIds.length === 0) return [];

    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collections_by_ids",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .select("*")
          .eq("user_id", userId)
          .in("id", collectionIds),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as SavedCollectionRow[];
  }

  async getCollection(userId: string, collectionId: string) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collections_get",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .select("*")
          .eq("user_id", userId)
          .eq("id", collectionId)
          .maybeSingle()
    );

    if (error) throw error;

    return (data as SavedCollectionRow | null) ?? null;
  }

  async createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ) {
    const { data, error } = await measureSavedPlacesDependency(
      "insert",
      "saved_collections_create",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .insert({
            user_id: userId,
            name: input.name,
            color_hex: input.colorHex ?? null,
            is_default: false
          })
          .select("*")
          .single()
    );

    if (error) throw error;

    return data as SavedCollectionRow;
  }

  async updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) {
    const update = buildCollectionUpdate(input);
    const { data, error } = await measureSavedPlacesDependency(
      "update",
      "saved_collections_update",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .update(update)
          .eq("user_id", userId)
          .eq("id", collectionId)
          .select("*")
          .maybeSingle()
    );

    if (error) throw error;

    return (data as SavedCollectionRow | null) ?? null;
  }

  async deleteCollection(userId: string, collectionId: string) {
    const { error } = await measureSavedPlacesDependency(
      "delete",
      "saved_collections_delete",
      async () =>
        getSupabaseClient()
          .from("saved_collections")
          .delete()
          .eq("user_id", userId)
          .eq("id", collectionId)
    );

    if (error) throw error;
  }

  async savePlace(userId: string, placeId: number) {
    const { data, error } = await measureSavedPlacesDependency(
      "upsert",
      "saved_places_save",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .upsert(
            { user_id: userId, place_id: placeId },
            { onConflict: "user_id,place_id", ignoreDuplicates: true }
          )
          .select("created_at")
          .maybeSingle()
    );

    if (error) throw error;
    if (data?.created_at) return data.created_at as string;

    return this.getSavedAt(userId, placeId);
  }

  async unsavePlace(userId: string, placeId: number) {
    const client = getSupabaseClient();
    const { error: membershipsError } = await measureSavedPlacesDependency(
      "delete",
      "saved_collection_places_unsave_memberships",
      async () =>
        client
          .from("saved_collection_places")
          .delete()
          .eq("user_id", userId)
          .eq("place_id", placeId)
    );

    if (membershipsError) throw membershipsError;

    const { error } = await measureSavedPlacesDependency(
      "delete",
      "saved_places_unsave",
      async () =>
        client
          .from("saved_places")
          .delete()
          .eq("user_id", userId)
          .eq("place_id", placeId)
    );

    if (error) throw error;
  }

  async addPlaceToCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    if (collectionIds.length === 0) return;

    const { error } = await measureSavedPlacesDependency(
      "upsert",
      "saved_collection_places_add",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .upsert(
            collectionIds.map((collectionId) => ({
              collection_id: collectionId,
              user_id: userId,
              place_id: placeId
            })),
            { onConflict: "collection_id,place_id", ignoreDuplicates: true }
          ),
      () => collectionIds.length
    );

    if (error) throw error;
  }

  async removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ) {
    const { error } = await measureSavedPlacesDependency(
      "delete",
      "saved_collection_places_remove",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .delete()
          .eq("user_id", userId)
          .eq("collection_id", collectionId)
          .eq("place_id", placeId)
    );

    if (error) throw error;
  }

  async listSavedPlaces(userId: string, limit: number) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_places_list",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select(SAVED_PLACE_COLUMNS)
          .eq("user_id", userId)
          .order("created_at", { ascending: false })
          .limit(limit),
      (result) => result.data?.length
    );

    if (error) throw error;

    return ((data ?? []) as unknown as SavedPlaceRow[]).map(mapSavedPlaceRow);
  }

  async countSavedPlaces(userId: string) {
    const { count, error } = await measureSavedPlacesDependency(
      "select",
      "saved_places_count",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select("*", { count: "exact", head: true })
          .eq("user_id", userId),
      (result) => result.count ?? undefined
    );

    if (error) throw error;

    return count ?? 0;
  }

  async listSavedPlaceIds(userId: string) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_places_ids",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select("place_id")
          .eq("user_id", userId)
          .order("created_at", { ascending: false }),
      (result) => result.data?.length
    );

    if (error) throw error;

    return ((data ?? []) as unknown as { place_id: number }[]).map(
      (row) => row.place_id
    );
  }

  async listCollectionPlaces(userId: string, collectionId: string) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_list",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .select(COLLECTION_PLACE_COLUMNS)
          .eq("user_id", userId)
          .eq("collection_id", collectionId)
          .order("sort_order", { ascending: true })
          .order("created_at", { ascending: true }),
      (result) => result.data?.length
    );

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
        const { error } = await measureSavedPlacesDependency(
          "update",
          "saved_collection_places_reorder",
          async () =>
            getSupabaseClient()
              .from("saved_collection_places")
              .update({ sort_order: sortOrder })
              .eq("user_id", userId)
              .eq("collection_id", collectionId)
              .eq("place_id", placeId)
        );

        if (error) throw error;
      })
    );
  }

  async getSavedPlaceStates(userId: string, placeIds: number[]) {
    if (placeIds.length === 0) return new Map<number, SavedPlaceState>();

    const states = await this.getSavedPlaceRows(userId, placeIds);
    const client = getSupabaseClient();
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_states",
      async () =>
        client
          .from("saved_collection_places")
          .select("place_id, collection_id")
          .eq("user_id", userId)
          .in("place_id", placeIds),
      (result) => result.data?.length
    );

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
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_places_created_at",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select("created_at")
          .eq("user_id", userId)
          .eq("place_id", placeId)
          .single()
    );

    if (error) throw error;

    return data.created_at as string;
  }

  private async getSavedPlaceRows(userId: string, placeIds: number[]) {
    const { data, error } = await measureSavedPlacesDependency(
      "select",
      "saved_places_states",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select("place_id")
          .eq("user_id", userId)
          .in("place_id", placeIds),
      (result) => result.data?.length
    );

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
