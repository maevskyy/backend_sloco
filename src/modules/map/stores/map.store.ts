import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  MapPlacesQuery,
  MapStoreContract,
  PlaceRow
} from "../common/map.types.js";

export class MapStore implements MapStoreContract {
  async placesInBbox(
    query: MapPlacesQuery,
    candidateLimit: number
  ): Promise<PlaceRow[]> {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "rpc",
        name: "map_places_in_bbox"
      },
      async () =>
        getSupabaseClient().rpc("map_places_in_bbox", {
          sw_lat: query.swLat,
          sw_lng: query.swLng,
          ne_lat: query.neLat,
          ne_lng: query.neLng,
          result_limit: candidateLimit
        }),
      (result) => result.data?.length
    );

    if (error) {
      throw error;
    }

    return (data ?? []) as unknown as PlaceRow[];
  }
}
