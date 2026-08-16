import { getPgPool } from "../../../lib/pg.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  RecServedStoreContract,
  RecServedWrite
} from "../common/feed.types.js";

// Serving "receipt" log (event-log spec 2.2), written asynchronously AFTER the
// feed response — never on its critical path. One round trip: the header row and
// all item rows travel in one statement; the items unpack from a single jsonb
// parameter (same pattern as the events_raw insert). ON CONFLICT DO NOTHING
// makes the retry after a failure idempotent.
export class RecServedStore implements RecServedStoreContract {
  async insertServing(write: RecServedWrite): Promise<void> {
    const items = write.items.map((item) => ({
      position: item.position,
      place_id: item.placeId,
      profile_id: item.profileId,
      score: item.score,
      score_components: item.scoreComponents
    }));

    await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "insert",
        name: "rec_served_insert"
      },
      async () =>
        getPgPool().query(
          `with serving as (
             insert into public.rec_served (
               request_id, user_id, server_ts, surface, city,
               algorithm_version, weights_preset, value_weights_version,
               config_overrides, profiles_count, fallback_used, latency_ms
             )
             values ($1, $2, now(), $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11)
             on conflict (request_id) do nothing
           )
           insert into public.rec_served_items (
             request_id, position, place_id, profile_id, score, score_components
           )
           select
             $1,
             (i->>'position')::int,
             i->>'place_id',
             (i->>'profile_id')::int,
             (i->>'score')::double precision,
             i->'score_components'
           from jsonb_array_elements($12::jsonb) as i
           on conflict (request_id, position) do nothing`,
          [
            write.requestId,
            write.userId,
            write.surface,
            write.city,
            write.algorithmVersion,
            write.weightsPreset,
            write.valueWeightsVersion,
            JSON.stringify(write.configOverrides),
            write.profilesCount,
            write.fallbackUsed,
            write.latencyMs,
            JSON.stringify(items)
          ]
        ),
      () => write.items.length
    );
  }
}
