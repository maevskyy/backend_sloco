import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  PlaceDetailRow,
  PlacePhotoRow,
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

  async placePhotos(source: string, sourceId: string): Promise<PlacePhotoRow[]> {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "select",
        name: "place_photos"
      },
      async () =>
        getSupabaseClient()
          .from("place_photos")
          .select("storage_path, public_url, width, height, photo_source")
          .eq("place_source", source)
          .eq("place_source_id", sourceId)
          .order("photo_index", { ascending: true, nullsFirst: false })
          .order("id", { ascending: true })
          .limit(20),
      (result) => result.data?.length
    );

    if (error) {
      throw error;
    }

    return (data ?? []) as unknown as PlacePhotoRow[];
  }
}
