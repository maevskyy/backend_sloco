import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  MapTileParams,
  MapTileStoreContract
} from "../common/map.tiles.js";

type MapTileRow = {
  tile: Buffer | null;
};

export class MapTileStore implements MapTileStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async getTile(params: MapTileParams): Promise<Buffer> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "rpc",
        name: "map_tile"
      },
      async () =>
        this.db.query<MapTileRow>(
          "select public.map_tile($1, $2, $3) as tile",
          [params.z, params.x, params.y]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows[0]?.tile ?? Buffer.alloc(0);
  }
}
