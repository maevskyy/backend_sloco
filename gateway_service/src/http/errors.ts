import type { FastifyReply, FastifyRequest } from "fastify";
import { ZodError } from "zod";

export const unauthorizedResponse = {
  status: "error",
  message: "Unauthorized"
} as const;

/**
 * Map errors that are common to every controller: zod validation → 400 (with the
 * module's message + issues), and anything else → 500. Controllers should check
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

  request.log.error(error);

  return reply.code(500).send({ status: "error" });
}
