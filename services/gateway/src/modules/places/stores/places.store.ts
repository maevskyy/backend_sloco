import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  PlaceDetailRow,
  PlacePhotoRow,
  PlacesStoreContract
} from "../common/places.types.js";

export class PlacesStore implements PlacesStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async placeDetailsById(placeId: number): Promise<PlaceDetailRow | null> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "rpc",
        name: "place_details_by_id"
      },
      async () =>
        this.db.query<PlaceDetailRow>(
          "select * from public.place_details_by_id(place_id => $1)",
          [placeId]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows[0] ?? null;
  }

  async placePhotos(source: string, sourceId: string): Promise<PlacePhotoRow[]> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "select",
        name: "place_photos"
      },
      async () =>
        this.db.query<PlacePhotoRow>(
          `select storage_path, public_url, width, height, photo_source
           from public.place_photos
           where place_source = $1
             and place_source_id = $2
           order by photo_index asc nulls last, id asc
           limit 20`,
          [source, sourceId]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }
}
