import type { FastifyRequest } from "fastify";
import {
  LogEvent,
  LogEventType,
  LogMessagePrefix
} from "../config/log-events.js";

export { LogMessagePrefix };

/**
 * Emit a compact response-summary log with the standard structured envelope.
 * Callers supply the path, summary fields, and the human-readable message.
 */
export function logResponseSummary(
  request: FastifyRequest,
  path: string,
  fields: Record<string, unknown>,
  message: string
) {
  request.log.info(
    {
      eventType: LogEventType.Response,
      event: LogEvent.ResponseSummary,
      path,
      ...fields
    },
    message
  );
}
