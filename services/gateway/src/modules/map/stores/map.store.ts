import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  MapPlacesQuery,
  MapStoreContract,
  PlaceRow
} from "../common/map.types.js";

export class MapStore implements MapStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  // bigint id and numeric scores come back as JSON numbers via the pool's
  // type parsers, same as the PostgREST response did.
  async placesInBbox(
    query: MapPlacesQuery,
    minScore: number,
    resultLimit: number
  ): Promise<PlaceRow[]> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "rpc",
        name: "map_places_in_bbox"
      },
      async () =>
        this.db.query<PlaceRow>(
          `select * from public.map_places_in_bbox(
             sw_lat => $1,
             sw_lng => $2,
             ne_lat => $3,
             ne_lng => $4,
             min_score => $5,
             result_limit => $6
           )`,
          [
            query.swLat,
            query.swLng,
            query.neLat,
            query.neLng,
            minScore,
            resultLimit
          ]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }
}
