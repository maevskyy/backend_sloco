import { getSupabaseClient } from "../../../lib/supabase.js";
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
    const { data, error } = await getSupabaseClient().rpc("places_in_bbox", {
      sw_lat: query.swLat,
      sw_lng: query.swLng,
      ne_lat: query.neLat,
      ne_lng: query.neLng,
      result_limit: candidateLimit
    });

    if (error) {
      throw error;
    }

    return (data ?? []) as unknown as PlaceRow[];
  }
}
