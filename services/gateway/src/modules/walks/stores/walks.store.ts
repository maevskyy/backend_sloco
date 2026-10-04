import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { WalksStoreContract } from "../common/walks.types.js";

// Walk places are Google CIDs of the sloco_ai catalog. Matching on
// (source, source_id) uses the places_source_source_id_key index.
const WALK_PLACE_SOURCE = "sloco_ai";

type PlaceIdRow = {
  source_id: string;
  id: number;
};

export class WalksStore implements WalksStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async placeIdsBySourceIds(sourceIds: string[]): Promise<Map<string, number>> {
    if (sourceIds.length === 0) return new Map();

    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "select",
        name: "walks_place_ids_by_source_ids"
      },
      async () =>
        this.db.query<PlaceIdRow>(
          `select source_id, id
             from public.places
            where source = $1
              and source_id = any($2::text[])`,
          [WALK_PLACE_SOURCE, sourceIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return new Map(result.rows.map((row) => [row.source_id, row.id]));
  }
}
