import { getPgPool } from "../../../lib/pg.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { EventRow, EventsStoreContract } from "../common/events.types.js";

// Direct Postgres (same pool as search/map tiles): the whole batch travels as
// ONE jsonb parameter and unpacks server-side, so a 500-event batch stays one
// round trip and never exceeds parameter limits. server_ts is stamped by the
// database at insert time (spec Part 1 rule 6).
export class EventsStore implements EventsStoreContract {
  async insertEvents(rows: EventRow[]): Promise<number> {
    if (rows.length === 0) {
      return 0;
    }

    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "insert",
        name: "events_raw_insert"
      },
      async () =>
        getPgPool().query(
          `insert into public.events_raw (
             event_id, event_type, known_type, user_id, anon_id, session_id,
             surface, request_id, position, place_id, client_ts, server_ts,
             seq, context, payload, device
           )
           select
             (e->>'event_id')::uuid,
             e->>'event_type',
             (e->>'known_type')::boolean,
             e->>'user_id',
             e->>'anon_id',
             e->>'session_id',
             e->>'surface',
             (e->>'request_id')::uuid,
             (e->>'position')::int,
             e->>'place_id',
             (e->>'client_ts')::timestamptz,
             now(),
             (e->>'seq')::bigint,
             e->'context',
             e->'payload',
             e->'device'
           from jsonb_array_elements($1::jsonb) as e
           on conflict (event_id) do nothing`,
          [JSON.stringify(rows)]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rowCount ?? 0;
  }

  async linkIdentities(userId: string, anonIds: string[]): Promise<void> {
    if (anonIds.length === 0) {
      return;
    }

    await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "insert",
        name: "identity_links_insert"
      },
      async () =>
        getPgPool().query(
          `insert into public.identity_links (anon_id, user_id)
           select distinct unnest($1::text[]), $2
           on conflict (anon_id, user_id) do nothing`,
          [anonIds, userId]
        )
    );
  }
}
