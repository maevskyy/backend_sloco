import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { HealthStoreContract } from "../common/health.types.js";

export class HealthStore implements HealthStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  // Reads one row from places through the same direct pool every store uses
  // (SLO-49), not PostgREST: proves the pool, the table and the SELECT grant
  // together, like the old HEAD select did, without its exact count.
  async checkConnection(): Promise<void> {
    await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "select",
        name: "health_places_head"
      },
      async () => this.db.query("select 1 from public.places limit 1")
    );
  }
}
