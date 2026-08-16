import type { z } from "zod";
import type { AuthenticatedUser } from "../../auth/auth.service.js";
import type {
  eventsBatchBodySchema,
  eventsAcceptedResponseSchema
} from "./events.schemas.js";

// One normalized events_raw row, ready for insertion. `user_id` comes from the
// auth token only — the body's user_id is ignored (spec Part 1 rule 1).
export type EventRow = {
  event_id: string;
  event_type: string;
  known_type: boolean;
  user_id: string | null;
  anon_id: string | null;
  session_id: string | null;
  surface: string | null;
  request_id: string | null;
  position: number | null;
  place_id: string | null;
  client_ts: string;
  seq: number | null;
  context: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
  device: Record<string, unknown> | null;
};

export type EventsBatchBody = z.infer<typeof eventsBatchBodySchema>;
export type EventsAcceptedResponse = z.infer<
  typeof eventsAcceptedResponseSchema
>;

export type EventsStoreContract = {
  /** Multi-row insert with ON CONFLICT (event_id) DO NOTHING; returns rows inserted. */
  insertEvents(rows: EventRow[]): Promise<number>;
  /** Idempotent (anon_id, user_id) links; conflict rows are ignored. */
  linkIdentities(userId: string, anonIds: string[]): Promise<void>;
};

export type EventsServiceInput = {
  body: EventsBatchBody;
  user: AuthenticatedUser | null;
};

export type EventsServiceContract = {
  ingestBatch(input: EventsServiceInput): Promise<EventsAcceptedResponse>;
};
