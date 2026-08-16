import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { sharedErrorResponses } from "../../../config/http-schemas.js";
import { eventsSchemaRegistry } from "./events.schemas.js";

export const eventsComponentSchemas = buildComponentSchemas(
  eventsSchemaRegistry
);

const defineRoute = makeDefineRoute({
  tag: "Events",
  errorResponses: sharedErrorResponses
});

export const eventsIngestRouteSchema = defineRoute({
  summary: "Ingest a batch of telemetry events.",
  description:
    "Append-only telemetry intake. Auth is optional: with a bearer token user_id " +
    "comes from the token (the body's user_id is ignored); without one events are " +
    "anonymous (anon_id only). Each event is validated on its own — an invalid " +
    "event lands in rejected[] while its neighbours are accepted. Resending the " +
    "same batch is safe: duplicates (same event_id) are counted, not inserted. " +
    "Unknown event_type values are stored with known_type=false, never errors. " +
    "Limits: 500 events per batch, 1 MiB body — over either limit the reply is 429. " +
    "Responds 202.",
  body: "EventsBatchBody",
  ok: "EventsAcceptedResponse",
  okStatus: 202
});
