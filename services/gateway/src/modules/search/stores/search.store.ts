import { getPgPool } from "../../../lib/pg.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  SearchPlaceRow,
  SearchStoreInput,
  SearchStoreContract
} from "../common/search.types.js";

// Direct Postgres, not PostgREST: search is latency-sensitive (fires per
// keystroke burst) and the HTTP hop through the Supabase API added a measured
// ~100ms + cold spikes on every request (TASKS_48). Same pattern as the map
// tile store.
export class SearchStore implements SearchStoreContract {
  async searchPlaces(input: SearchStoreInput): Promise<SearchPlaceRow[]> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "rpc",
        name: "search_places"
      },
      async () =>
        getPgPool().query<SearchPlaceRow>(
          `select * from public.search_places(
             q => $1,
             user_lat => $2,
             user_lng => $3,
             user_city => $4,
             user_country => $5,
             result_limit => $6,
             category_keywords => $7,
             radius_meters => $8
           )`,
          [
            input.q,
            input.lat,
            input.lng,
            input.city,
            input.country,
            input.limit,
            input.categoryKeywords,
            input.radiusMeters
          ]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }
}
