import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  FeedPlaceRow,
  FeedPlacesQuery,
  FeedUserSignals,
  FeedStoreContract
} from "../common/feed.types.js";

const WANT_TO_GO_COLLECTION_NAME = "want to go";

// Signal rows come back flat: the PostgREST embeds (places!inner(source_id),
// saved_collections!inner(...)) are plain inner JOINs now. Timestamps are
// to_json-format strings (json_agg output), so localeCompare still orders them.
type SavedSignalRow = {
  place_id: number;
  created_at: string;
  source_id: string | null;
};

type SavedCollectionSignalRow = {
  place_id: number;
  sort_order: number;
  created_at: string;
  source_id: string | null;
  collection_name: string;
  collection_slug: string | null;
  collection_is_default: boolean;
};

type ReactionSignalRow = {
  source_id: string;
  reaction: "favorite" | "dislike" | "hide";
  updated_at: string;
};

type SignalSetsRow = {
  saved: SavedSignalRow[];
  collections: SavedCollectionSignalRow[];
  reactions: ReactionSignalRow[];
};

function measureFeedDependency<T>(
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

export class FeedStore implements FeedStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async getUserSignals(userId: string): Promise<FeedUserSignals> {
    const { savedRows, collectionRows, reactionRows } =
      await this.getSignalRows(userId);

    const allSaved = dedupe(
      savedRows
        .sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((row) => row.source_id)
    );
    const wantToGo = dedupe(
      collectionRows
        .filter((row) => isWantToGoCollection(row))
        .sort(
          (a, b) =>
            a.sort_order - b.sort_order || a.created_at.localeCompare(b.created_at)
        )
        .map((row) => row.source_id)
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
    // "Been there" records a VISIT, not a preference (TASKS_54): a place whose only
    // list is Been there must not become a strong favourite. Visiting a place the
    // user also filed elsewhere still counts — that membership speaks for itself.
    const beenOnly = new Set(
      dedupe(
        collectionRows
          .filter((row) => row.collection_slug === "been")
          .map((row) => row.source_id)
      ).filter(
        (sourceId) =>
          !collectionRows.some(
            (row) =>
              row.collection_slug !== "been" &&
              row.source_id === sourceId
          )
      )
    );
    const derivedFavourites = allSaved.filter(
      (sourceId) => !wantToGoSet.has(sourceId) && !beenOnly.has(sourceId)
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

    const result = await measureFeedDependency(
      "rpc",
      "feed_places_by_source_ids",
      async () =>
        this.db.query<FeedPlaceRow>(
          `select * from public.feed_places_by_source_ids(
             source_ids => $1,
             user_lat => $2,
             user_lng => $3,
             result_limit => $4
           )`,
          [sourceIds, query.lat ?? null, query.lng ?? null, limit]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }

  async fallbackFeedPlaces(
    query: FeedPlacesQuery,
    limit: number,
    categoryKeywords: string[] | null
  ): Promise<FeedPlaceRow[]> {
    const result = await measureFeedDependency(
      "rpc",
      "feed_fallback_places",
      async () =>
        this.db.query<FeedPlaceRow>(
          `select * from public.feed_fallback_places(
             user_lat => $1,
             user_lng => $2,
             user_city => $3,
             user_country => $4,
             result_limit => $5,
             category_keywords => $6
           )`,
          [
            query.lat ?? null,
            query.lng ?? null,
            query.city ?? null,
            query.country ?? null,
            limit,
            categoryKeywords
          ]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }

  // All three signal sets in ONE statement on one pooled client: run as three
  // parallel queries they took 3 of PG_POOL_MAX clients per feed request, and
  // the app fires one feed request per pill at login. Each set is a json array
  // (json_agg); json renders timestamptz in to_json format, so the rows match
  // what the per-pool parsers return for plain columns.
  private async getSignalRows(userId: string) {
    const result = await measureFeedDependency(
      "select",
      "feed_user_signals",
      async () =>
        this.db.query<SignalSetsRow>(
          `select
             (
               select coalesce(
                 json_agg(
                   json_build_object(
                     'place_id', sp.place_id,
                     'created_at', sp.created_at,
                     'source_id', p.source_id
                   )
                   order by sp.created_at desc
                 ),
                 '[]'::json
               )
               from public.saved_places sp
               join public.places p on p.id = sp.place_id
               where sp.user_id = $1
             ) as saved,
             (
               select coalesce(
                 json_agg(
                   json_build_object(
                     'place_id', scp.place_id,
                     'sort_order', scp.sort_order,
                     'created_at', scp.created_at,
                     'source_id', p.source_id,
                     'collection_name', sc.name,
                     'collection_slug', sc.slug,
                     'collection_is_default', sc.is_default
                   )
                 ),
                 '[]'::json
               )
               from public.saved_collection_places scp
               join public.places p on p.id = scp.place_id
               join public.saved_collections sc on sc.id = scp.collection_id
               where scp.user_id = $1
             ) as collections,
             (
               select coalesce(
                 json_agg(
                   json_build_object(
                     'source_id', pr.source_id,
                     'reaction', pr.reaction,
                     'updated_at', pr.updated_at
                   )
                 ),
                 '[]'::json
               )
               from public.place_reactions pr
               where pr.user_id = $1
             ) as reactions`,
          [userId]
        ),
      (queryResult) => {
        const row = queryResult.rows[0];
        return row
          ? row.saved.length + row.collections.length + row.reactions.length
          : undefined;
      }
    );

    const row = result.rows[0];

    return {
      savedRows: row?.saved ?? [],
      collectionRows: row?.collections ?? [],
      reactionRows: row?.reactions ?? []
    };
  }
}

// The quick-save bucket — the list a save with no explicit choice lands in. It is
// the system "saved" list since TASKS_54, was the default collection before that,
// and was literally named "Want to go" before TASKS_53; all three are accepted so
// old rows keep their meaning.
function isWantToGoCollection(row: SavedCollectionSignalRow) {
  return (
    row.collection_slug === "saved" ||
    row.collection_is_default ||
    row.collection_name.trim().toLowerCase() === WANT_TO_GO_COLLECTION_NAME
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
