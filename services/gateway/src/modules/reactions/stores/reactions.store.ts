import { getDb, type Db } from "../../../lib/db.js";
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
      dependency: "postgres",
      operation,
      name
    },
    callback,
    getRowsCount
  );
}

export class ReactionsStore implements ReactionsStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async setReaction(userId: string, placeId: number, reaction: PlaceReaction) {
    const sourceId = await this.getRequiredSourceId(placeId);
    // Same columns as the PostgREST upsert payload: created_at is only set
    // by its default on insert, never overwritten on conflict.
    await measureReactionsDependency("upsert", "place_reactions_set", async () =>
      this.db.query(
        `insert into public.place_reactions (user_id, source_id, reaction, updated_at)
         values ($1, $2, $3, $4)
         on conflict (user_id, source_id) do update
           set reaction = excluded.reaction,
               updated_at = excluded.updated_at`,
        [userId, sourceId, reaction, new Date().toISOString()]
      )
    );
  }

  async deleteReaction(userId: string, placeId: number) {
    const sourceId = await this.getRequiredSourceId(placeId);
    await measureReactionsDependency("delete", "place_reactions_delete", async () =>
      this.db.query(
        `delete from public.place_reactions
         where user_id = $1
           and source_id = $2`,
        [userId, sourceId]
      )
    );
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

    const result = await measureReactionsDependency(
      "select",
      "place_reactions_by_place_ids",
      async () =>
        this.db.query<PlaceReactionRow>(
          `select source_id, reaction
           from public.place_reactions
           where user_id = $1
             and source_id = any($2)`,
          [userId, sourceIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    const reactions = new Map<number, PlaceReaction>();

    for (const row of result.rows) {
      for (const placeId of sourceIdToPlaceIds.get(row.source_id) ?? []) {
        reactions.set(placeId, row.reaction);
      }
    }

    return reactions;
  }

  private async getRequiredSourceId(placeId: number) {
    // places.id is the primary key: at most one row, as .maybeSingle() assumed.
    const result = await measureReactionsDependency(
      "select",
      "places_source_id_by_id",
      async () =>
        this.db.query<{ source_id: string | null }>(
          `select source_id
           from public.places
           where id = $1`,
          [placeId]
        )
    );
    const sourceId = result.rows[0]?.source_id;

    if (!sourceId) {
      throw new PlaceNotFoundError(placeId);
    }

    return sourceId;
  }

  private async getReactionRows(userId: string) {
    const result = await measureReactionsDependency(
      "select",
      "place_reactions_list",
      async () =>
        this.db.query<PlaceReactionRow>(
          `select source_id, reaction
           from public.place_reactions
           where user_id = $1`,
          [userId]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }

  private async getPlaceIdsBySourceIds(sourceIds: string[]) {
    if (sourceIds.length === 0) {
      return new Map<string, number[]>();
    }

    // id is bigserial: a JS number via the pool's int8 parser.
    const result = await measureReactionsDependency(
      "select",
      "places_by_source_ids",
      async () =>
        this.db.query<PlaceSourceIdRow>(
          `select id, source_id
           from public.places
           where source_id = any($1)`,
          [[...new Set(sourceIds)]]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    const placeIdsBySourceId = new Map<string, number[]>();

    for (const row of result.rows) {
      const existing = placeIdsBySourceId.get(row.source_id) ?? [];
      existing.push(row.id);
      placeIdsBySourceId.set(row.source_id, existing);
    }

    return placeIdsBySourceId;
  }

  private async getSourceIdsByPlaceIds(placeIds: number[]) {
    const result = await measureReactionsDependency(
      "select",
      "places_source_ids_by_ids",
      async () =>
        this.db.query<PlaceSourceIdRow>(
          `select id, source_id
           from public.places
           where id = any($1)`,
          [[...new Set(placeIds)]]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return new Map(result.rows.map((row) => [row.id, row.source_id]));
  }
}
