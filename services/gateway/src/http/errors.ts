import type { FastifyReply, FastifyRequest } from "fastify";
import { ZodError } from "zod";
import { isDbUnavailableError } from "../lib/db-errors.js";

export const unauthorizedResponse = {
  status: "error",
  message: "Unauthorized"
} as const;

export const serviceUnavailableResponse = {
  status: "error",
  message: "Service temporarily unavailable"
} as const;

// Seconds the client should wait before retrying a 503.
const DB_UNAVAILABLE_RETRY_AFTER_SECONDS = "1";

/**
 * Database saturated or too slow (pool wait / query deadline exceeded) → 503
 * with Retry-After, never 500: the request was fine, retrying shortly will
 * likely succeed. Returns undefined when `error` is something else.
 */
export function sendDbUnavailable(
  request: FastifyRequest,
  reply: FastifyReply,
  error: unknown
) {
  if (!isDbUnavailableError(error)) {
    return undefined;
  }

  request.log.warn({ err: error }, "database unavailable, answering 503");

  return reply
    .code(503)
    .header("Retry-After", DB_UNAVAILABLE_RETRY_AFTER_SECONDS)
    .send(serviceUnavailableResponse);
}

/**
 * Map errors that are common to every controller: zod validation → 400 (with the
 * module's message + issues), database unavailable → 503, and anything else → 500. Controllers should check
 * their own domain errors first, then delegate here.
 */
export function handleCommonError(
  request: FastifyRequest,
  reply: FastifyReply,
  error: unknown,
  validationMessage = "Invalid request"
) {
  if (error instanceof ZodError) {
    return reply.code(400).send({
      status: "error",
      message: validationMessage,
      issues: error.issues
    });
  }

  const unavailable = sendDbUnavailable(request, reply, error);

  if (unavailable) {
    return unavailable;
  }

  request.log.error(error);

  return reply.code(500).send({ status: "error" });
}
