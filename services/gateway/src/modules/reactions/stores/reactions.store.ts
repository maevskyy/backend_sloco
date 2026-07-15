import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import { PlaceNotFoundError } from "../common/reactions.errors.js";
import type {
  PlaceReaction,
  PlaceReactionRow,
  PlaceSourceIdRow,
  ReactionsResult,
  ReactionsStoreContract
} from "../common/reactions.types.js";

function measureReactionsDependency<T>(
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

export class ReactionsStore implements ReactionsStoreContract {
  async setReaction(userId: string, placeId: number, reaction: PlaceReaction) {
    const sourceId = await this.getRequiredSourceId(placeId);
    const { error } = await measureReactionsDependency(
      "upsert",
      "place_reactions_set",
      async () =>
        getSupabaseClient()
          .from("place_reactions")
          .upsert(
            {
              user_id: userId,
              source_id: sourceId,
              reaction,
              updated_at: new Date().toISOString()
            },
            { onConflict: "user_id,source_id" }
          )
    );

    if (error) throw error;
  }

  async deleteReaction(userId: string, placeId: number) {
    const sourceId = await this.getRequiredSourceId(placeId);
    const { error } = await measureReactionsDependency(
      "delete",
      "place_reactions_delete",
      async () =>
        getSupabaseClient()
          .from("place_reactions")
          .delete()
          .eq("user_id", userId)
          .eq("source_id", sourceId)
    );

    if (error) throw error;
  }

  async listReactions(userId: string): Promise<ReactionsResult> {
    const rows = await this.getReactionRows(userId);

    if (rows.length === 0) {
      return {
        favorites: [],
        dislikes: [],
        hidden: []
      };
    }

    const placeIdsBySourceId = await this.getPlaceIdsBySourceIds(
      rows.map((row) => row.source_id)
    );

    const grouped = {
      favorites: [] as number[],
      dislikes: [] as number[],
      hidden: [] as number[]
    };

    for (const row of rows) {
      const placeIds = placeIdsBySourceId.get(row.source_id) ?? [];

      if (row.reaction === "favorite") {
        grouped.favorites.push(...placeIds);
      } else if (row.reaction === "dislike") {
        grouped.dislikes.push(...placeIds);
      } else {
        grouped.hidden.push(...placeIds);
      }
    }

    grouped.favorites.sort((left, right) => left - right);
    grouped.dislikes.sort((left, right) => left - right);
    grouped.hidden.sort((left, right) => left - right);

    return grouped;
  }

  async getReactions(userId: string, placeIds: number[]) {
    if (placeIds.length === 0) {
      return new Map<number, PlaceReaction>();
    }

    const sourceIdsByPlaceId = await this.getSourceIdsByPlaceIds(placeIds);
    const sourceIdToPlaceIds = new Map<string, number[]>();

    for (const [placeId, sourceId] of sourceIdsByPlaceId.entries()) {
      const existing = sourceIdToPlaceIds.get(sourceId) ?? [];
      existing.push(placeId);
      sourceIdToPlaceIds.set(sourceId, existing);
    }

    const sourceIds = [...sourceIdToPlaceIds.keys()];

    if (sourceIds.length === 0) {
      return new Map<number, PlaceReaction>();
    }

    const { data, error } = await measureReactionsDependency(
      "select",
      "place_reactions_by_place_ids",
      async () =>
        getSupabaseClient()
          .from("place_reactions")
          .select("source_id,reaction")
          .eq("user_id", userId)
          .in("source_id", sourceIds),
      (result) => result.data?.length
    );

    if (error) throw error;

    const reactions = new Map<number, PlaceReaction>();

    for (const row of (data ?? []) as unknown as PlaceReactionRow[]) {
      for (const placeId of sourceIdToPlaceIds.get(row.source_id) ?? []) {
        reactions.set(placeId, row.reaction);
      }
    }

    return reactions;
  }

  private async getRequiredSourceId(placeId: number) {
    const { data, error } = await measureReactionsDependency(
      "select",
      "places_source_id_by_id",
      async () =>
        getSupabaseClient()
          .from("places")
          .select("source_id")
          .eq("id", placeId)
          .maybeSingle()
    );

    if (error) throw error;
    if (!data?.source_id) {
      throw new PlaceNotFoundError(placeId);
    }

    return data.source_id as string;
  }

  private async getReactionRows(userId: string) {
    const { data, error } = await measureReactionsDependency(
      "select",
      "place_reactions_list",
      async () =>
        getSupabaseClient()
          .from("place_reactions")
          .select("source_id,reaction")
          .eq("user_id", userId),
      (result) => result.data?.length
    );

    if (error) throw error;

    return (data ?? []) as unknown as PlaceReactionRow[];
  }

  private async getPlaceIdsBySourceIds(sourceIds: string[]) {
    if (sourceIds.length === 0) {
      return new Map<string, number[]>();
    }

    const { data, error } = await measureReactionsDependency(
      "select",
      "places_by_source_ids",
      async () =>
        getSupabaseClient()
          .from("places")
          .select("id,source_id")
          .in("source_id", [...new Set(sourceIds)]),
      (result) => result.data?.length
    );

    if (error) throw error;

    const placeIdsBySourceId = new Map<string, number[]>();

    for (const row of (data ?? []) as unknown as PlaceSourceIdRow[]) {
      const existing = placeIdsBySourceId.get(row.source_id) ?? [];
      existing.push(row.id);
      placeIdsBySourceId.set(row.source_id, existing);
    }

    return placeIdsBySourceId;
  }

  private async getSourceIdsByPlaceIds(placeIds: number[]) {
    const { data, error } = await measureReactionsDependency(
      "select",
      "places_source_ids_by_ids",
      async () =>
        getSupabaseClient()
          .from("places")
          .select("id,source_id")
          .in("id", [...new Set(placeIds)]),
      (result) => result.data?.length
    );

    if (error) throw error;

    return new Map(
      ((data ?? []) as unknown as PlaceSourceIdRow[]).map((row) => [
        row.id,
        row.source_id
      ])
    );
  }
}
