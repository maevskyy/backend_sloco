import type { FastifyReply, FastifyRequest } from "fastify";
import {
  extractBearerToken,
  type AuthService,
  type AuthenticatedUser
} from "../modules/auth/auth.service.js";
import { unauthorizedResponse } from "./errors.js";

// Shared bearer-auth resolution. Controllers compose these with their own
// error handling; the guard only knows the `AuthService` contract.
export function createAuthGuard(authService: AuthService) {
  return {
    /**
     * Require a valid bearer token. Sends 401 and returns `null` when the token
     * is missing or invalid; otherwise returns the user.
     */
    async requireUser(
      request: FastifyRequest,
      reply: FastifyReply
    ): Promise<AuthenticatedUser | null> {
      const token = extractBearerToken(request.headers.authorization);

      if (!token) {
        reply.code(401).send(unauthorizedResponse);
        return null;
      }

      const user = await authService.getUserFromToken(token);

      if (!user) {
        reply.code(401).send(unauthorizedResponse);
        return null;
      }

      return user;
    },

    /**
     * Optional auth: no `Authorization` header → `null` (proceed unauthenticated);
     * a present-but-invalid token → `"invalid"` (caller should send 401).
     */
    async optionalUser(
      request: FastifyRequest
    ): Promise<AuthenticatedUser | "invalid" | null> {
      const { authorization } = request.headers;

      if (authorization === undefined) {
        return null;
      }

      const token = extractBearerToken(authorization);

      if (!token) {
        return "invalid";
      }

      return (await authService.getUserFromToken(token)) ?? "invalid";
    }
  };
}

export type AuthGuard = ReturnType<typeof createAuthGuard>;
