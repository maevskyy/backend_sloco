import { z } from "zod";
import { KNOWN_EVENT_TYPES } from "../common/events.schemas.js";
import type {
  EventRow,
  EventsAcceptedResponse,
  EventsServiceContract,
  EventsServiceInput,
  EventsStoreContract
} from "../common/events.types.js";
import { EventsStore } from "../stores/events.store.js";

const uuidSchema = z.uuid();

type RejectedEvent = EventsAcceptedResponse["rejected"][number];

export function createEventsService(
  store: EventsStoreContract = new EventsStore()
): EventsServiceContract {
  return {
    async ingestBatch({
      body,
      user
    }: EventsServiceInput): Promise<EventsAcceptedResponse> {
      const rows: EventRow[] = [];
      const rejected: RejectedEvent[] = [];
      const device = asObject(body.device) ?? null;
      const userId = user?.id ?? null;

      for (const rawEvent of body.events) {
        const outcome = normalizeEvent(rawEvent, userId, device);

        if ("reason" in outcome) {
          rejected.push(outcome);
          continue;
        }

        rows.push(outcome);
      }

      const inserted = await store.insertEvents(rows);

      // Login stitching (spec Part 1): an authenticated batch that still carries
      // the device's anon_id links the pre-login history to the account. Events
      // themselves are never updated — readers join through identity_links.
      if (userId) {
        const anonIds = [
          ...new Set(rows.flatMap((row) => (row.anon_id ? [row.anon_id] : [])))
        ];
        await store.linkIdentities(userId, anonIds);
      }

      return {
        accepted: inserted,
        // A duplicate is a valid event whose event_id already existed (batch
        // retries are expected and safe — spec Part 1 rule 4).
        duplicates: rows.length - inserted,
        rejected
      };
    }
  };
}

// Per-event validation (spec Part 1 rule 3): exactly three things decide
// acceptance — event_id (uuid), event_type, parseable client_ts. Everything
// else is stored as sent, or as null when it does not fit its column.
function normalizeEvent(
  rawEvent: unknown,
  userId: string | null,
  device: Record<string, unknown> | null
): EventRow | RejectedEvent {
  const event = asObject(rawEvent);

  if (!event) {
    return { event_id: null, reason: "not_an_object" };
  }

  const eventId = asUuid(event.event_id);
  if (!eventId) {
    return {
      event_id: typeof event.event_id === "string" ? event.event_id : null,
      reason: "bad_event_id"
    };
  }

  const eventType = asNonEmptyString(event.event_type);
  if (!eventType) {
    return { event_id: eventId, reason: "bad_event_type" };
  }

  const clientTs = asParseableTimestamp(event.client_ts);
  if (!clientTs) {
    return { event_id: eventId, reason: "bad_client_ts" };
  }

  return {
    event_id: eventId,
    event_type: eventType,
    known_type: KNOWN_EVENT_TYPES.has(eventType),
    // Never trust the body's user_id — token only (spec Part 1 rule 1).
    user_id: userId,
    anon_id: asNonEmptyString(event.anon_id),
    session_id: asNonEmptyString(event.session_id),
    surface: asNonEmptyString(event.surface),
    request_id: asUuid(contextOf(event).request_id),
    position: asInteger(contextOf(event).position),
    place_id: asNonEmptyString(event.place_id),
    client_ts: clientTs,
    seq: asInteger(event.seq),
    context: asObject(event.context),
    payload: asObject(event.payload),
    device
  };
}

function contextOf(event: Record<string, unknown>): Record<string, unknown> {
  return asObject(event.context) ?? {};
}

function asObject(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function asNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asUuid(value: unknown): string | null {
  return typeof value === "string" && uuidSchema.safeParse(value).success
    ? value
    : null;
}

function asInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function asParseableTimestamp(value: unknown): string | null {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    return null;
  }
  return value;
}

export const eventsService = createEventsService();
