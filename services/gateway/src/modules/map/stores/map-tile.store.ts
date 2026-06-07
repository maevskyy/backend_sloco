import { getPgPool } from "../../../lib/pg.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  MapTileParams,
  MapTileStoreContract
} from "../common/map.tiles.js";

type MapTileRow = {
  tile: Buffer | null;
};

export class MapTileStore implements MapTileStoreContract {
  async getTile(params: MapTileParams): Promise<Buffer> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "rpc",
        name: "map_tile"
      },
      async () =>
        getPgPool().query<MapTileRow>(
          "select public.map_tile($1, $2, $3) as tile",
          [params.z, params.x, params.y]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows[0]?.tile ?? Buffer.alloc(0);
  }
}
