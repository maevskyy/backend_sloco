import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  SearchPlaceRow,
  SearchPlacesQuery,
  SearchStoreContract
} from "../common/search.types.js";

export class SearchStore implements SearchStoreContract {
  async searchPlaces(query: SearchPlacesQuery): Promise<SearchPlaceRow[]> {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "rpc",
        name: "search_places"
      },
      async () =>
        getSupabaseClient().rpc("search_places", {
          q: query.q,
          user_lat: query.lat ?? null,
          user_lng: query.lng ?? null,
          user_city: query.city ?? null,
          user_country: query.country ?? null,
          result_limit: query.limit
        }),
      (result) => result.data?.length
    );

    if (error) {
      throw error;
    }

    return (data ?? []) as unknown as SearchPlaceRow[];
  }
}
