import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  FeedPlaceRow,
  FeedPlacesQuery,
  FeedUserSignals,
  FeedStoreContract
} from "../common/feed.types.js";

const WANT_TO_GO_COLLECTION_NAME = "want to go";

type SavedSignalPlace = {
  source_id: string | null;
};

type SavedSignalRow = {
  place_id: number;
  created_at: string;
  places: SavedSignalPlace | SavedSignalPlace[] | null;
};

type SavedCollectionSignalRow = {
  place_id: number;
  sort_order: number;
  created_at: string;
  places: SavedSignalPlace | SavedSignalPlace[] | null;
  saved_collections:
    | {
        name: string;
        is_default: boolean;
      }
    | {
        name: string;
        is_default: boolean;
      }[]
    | null;
};

type ReactionSignalRow = {
  source_id: string;
  reaction: "favorite" | "dislike" | "hide";
  updated_at: string;
};

function measureFeedDependency<T>(
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

export class FeedStore implements FeedStoreContract {
  async getUserSignals(userId: string): Promise<FeedUserSignals> {
    const [savedRows, collectionRows, reactionRows] = await Promise.all([
      this.getSavedPlaceRows(userId),
      this.getSavedCollectionPlaceRows(userId),
      this.getReactionRows(userId)
    ]);

    const allSaved = dedupe(
      savedRows
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((row) => normalizePlaceRecord(row.places)?.source_id)
    );
    const wantToGo = dedupe(
      collectionRows
        .filter((row) => isWantToGoCollection(row.saved_collections))
        .sort(
          (a, b) =>
            a.sort_order - b.sort_order || a.created_at.localeCompare(b.created_at)
        )
        .map((row) => normalizePlaceRecord(row.places)?.source_id)
    );
    const wantToGoSet = new Set(wantToGo);
    const explicitFavorites = dedupe(
      reactionRows
        .filter((row) => row.reaction === "favorite")
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map((row) => row.source_id)
    );
    const dislikes = dedupe(
      reactionRows
        .filter((row) => row.reaction === "dislike")
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map((row) => row.source_id)
    );
    const hidden = dedupe(
      reactionRows
        .filter((row) => row.reaction === "hide")
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map((row) => row.source_id)
    );
    const derivedFavourites = allSaved.filter(
      (sourceId) => !wantToGoSet.has(sourceId)
    );
    const fallbackFavourites =
      derivedFavourites.length > 0 || wantToGo.length > 0
        ? derivedFavourites
        : allSaved;

    return {
      favouritesPlaceIds: dedupe([...explicitFavorites, ...fallbackFavourites]),
      wantToGoPlaceIds: wantToGo,
      dislikePlaceIds: dislikes,
      hidePlaceIds: hidden
    };
  }

  async feedPlacesBySourceIds(
    sourceIds: string[],
    query: FeedPlacesQuery,
    limit: number
  ): Promise<FeedPlaceRow[]> {
    if (sourceIds.length === 0) return [];

    const { data, error } = await measureFeedDependency(
      "rpc",
      "feed_places_by_source_ids",
      async () =>
        getSupabaseClient().rpc("feed_places_by_source_ids", {
          source_ids: sourceIds,
          user_lat: query.lat ?? null,
          user_lng: query.lng ?? null,
          result_limit: limit
        }),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as FeedPlaceRow[];
  }

  async fallbackFeedPlaces(
    query: FeedPlacesQuery,
    limit: number,
    categoryKeywords: string[] | null
  ): Promise<FeedPlaceRow[]> {
    const { data, error } = await measureFeedDependency(
      "rpc",
      "feed_fallback_places",
      async () =>
        getSupabaseClient().rpc("feed_fallback_places", {
          user_lat: query.lat ?? null,
          user_lng: query.lng ?? null,
          user_city: query.city ?? null,
          user_country: query.country ?? null,
          result_limit: limit,
          category_keywords: categoryKeywords
        }),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as FeedPlaceRow[];
  }

  private async getSavedPlaceRows(userId: string) {
    const { data, error } = await measureFeedDependency(
      "select",
      "feed_saved_signal_places",
      async () =>
        getSupabaseClient()
          .from("saved_places")
          .select("place_id, created_at, places!inner(source_id)")
          .eq("user_id", userId)
          .order("created_at", { ascending: false }),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as SavedSignalRow[];
  }

  private async getSavedCollectionPlaceRows(userId: string) {
    const { data, error } = await measureFeedDependency(
      "select",
      "feed_saved_collection_signal_places",
      async () =>
        getSupabaseClient()
          .from("saved_collection_places")
          .select(
            [
              "place_id",
              "sort_order",
              "created_at",
              "places!inner(source_id)",
              "saved_collections!inner(name,is_default)"
            ].join(",")
          )
          .eq("user_id", userId),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as SavedCollectionSignalRow[];
  }

  private async getReactionRows(userId: string) {
    const { data, error } = await measureFeedDependency(
      "select",
      "feed_reaction_signal_places",
      async () =>
        getSupabaseClient()
          .from("place_reactions")
          .select("source_id,reaction,updated_at")
          .eq("user_id", userId),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as ReactionSignalRow[];
  }
}

function normalizePlaceRecord(
  place: SavedSignalPlace | SavedSignalPlace[] | null
) {
  return Array.isArray(place) ? place[0] : place;
}

function normalizeCollectionRecord(
  collection: SavedCollectionSignalRow["saved_collections"]
) {
  return Array.isArray(collection) ? collection[0] : collection;
}

function isWantToGoCollection(
  collection: SavedCollectionSignalRow["saved_collections"]
) {
  const normalized = normalizeCollectionRecord(collection);

  if (!normalized) return false;

  return (
    normalized.is_default ||
    normalized.name.trim().toLowerCase() === WANT_TO_GO_COLLECTION_NAME
  );
}

function dedupe(values: Array<string | null | undefined>) {
  const result: string[] = [];
  const seen = new Set<string>();

  for (const value of values) {
    const normalized = value?.trim();

    if (!normalized || seen.has(normalized)) continue;

    seen.add(normalized);
    result.push(normalized);
  }

  return result;
}
