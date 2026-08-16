import { z } from "zod";

// Client event dictionary (frontend event-log spec). Types OUTSIDE this list are
// still accepted and stored with known_type=false — a newer app must never lose
// events to an older backend (spec Part 1 rule 5). Keep in sync with
// FRONTEND_EVENTS_API.md when the app adds types.
export const KNOWN_EVENT_TYPES = new Set([
  "impression",
  "map_viewport",
  "card_open",
  "card_dwell",
  "photo_swipe",
  "similar_open",
  "save_favourite",
  "save_want_to_go",
  "unsave_favourite",
  "unsave_want_to_go",
  "dislike",
  "hide",
  "share",
  "route_click",
  "external_click",
  "search_query",
  "search_result_click",
  "filter_apply",
  "onboarding_card_like",
  "onboarding_complete",
  "onboarding_skip",
  "app_open",
  "app_background"
]);

export const MAX_EVENTS_PER_BATCH = 500;
export const MAX_BATCH_BODY_BYTES = 1_048_576; // 1 MiB

const jsonObjectSchema = z.record(z.string(), z.unknown());

// The envelope is validated as a whole (bad envelope -> 400); the events INSIDE
// are validated one by one in the service (bad event -> rejected[], neighbours
// are still accepted — spec Part 1 rule 3). Hence z.unknown() items here.
export const eventsBatchBodySchema = z.object({
  batch_id: z.uuid(),
  device: jsonObjectSchema.optional(),
  events: z.array(z.unknown())
});

// One client event, as documented for OpenAPI. Only event_id / event_type /
// client_ts decide acceptance; everything else is stored as sent (or null).
export const eventInputSchema = z.object({
  event_id: z.uuid(),
  event_type: z.string().min(1),
  client_ts: z.string(),
  seq: z.number().int().optional(),
  user_id: z.string().optional().describe("Ignored: taken from the auth token."),
  anon_id: z.string().optional(),
  session_id: z.string().optional(),
  surface: z.string().optional(),
  context: jsonObjectSchema.optional(),
  place_id: z.string().optional(),
  payload: jsonObjectSchema.optional()
});

export const eventsRejectedItemSchema = z.object({
  event_id: z.string().nullable(),
  reason: z.string()
});

export const eventsAcceptedResponseSchema = z.object({
  accepted: z.number().int().min(0),
  duplicates: z.number().int().min(0),
  rejected: z.array(eventsRejectedItemSchema)
});

export const eventsSchemaRegistry = z.registry<{ id: string }>();

eventsSchemaRegistry.add(eventsBatchBodySchema, { id: "EventsBatchBody" });
eventsSchemaRegistry.add(eventInputSchema, { id: "EventInput" });
eventsSchemaRegistry.add(eventsRejectedItemSchema, {
  id: "EventsRejectedItem"
});
eventsSchemaRegistry.add(eventsAcceptedResponseSchema, {
  id: "EventsAcceptedResponse"
});
