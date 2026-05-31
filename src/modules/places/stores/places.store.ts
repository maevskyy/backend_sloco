import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  PlaceDetailRow,
  PlacesStoreContract
} from "../common/places.types.js";

export class PlacesStore implements PlacesStoreContract {
  async placeDetailsById(placeId: number): Promise<PlaceDetailRow | null> {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "rpc",
        name: "place_details_by_id"
      },
      async () =>
        getSupabaseClient().rpc("place_details_by_id", {
          place_id: placeId
        }),
      (result) => (Array.isArray(result.data) ? result.data.length : undefined)
    );

    if (error) {
      throw error;
    }

    const rows = (data ?? []) as unknown as PlaceDetailRow[];

    return rows[0] ?? null;
  }
}
