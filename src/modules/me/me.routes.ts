import type { FastifyInstance } from "fastify";
import { AppRoute } from "../../config/routes.js";
import {
  extractBearerToken,
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { getMe, type MeService } from "./me.service.js";
import { meRouteSchema } from "./me.openapi.js";

type MeRoutesOptions = {
  authService?: AuthService;
  meService?: MeService;
};

const unauthorizedResponse = {
  status: "error",
  message: "Unauthorized"
} as const;

export async function registerMeRoutes(
  app: FastifyInstance,
  options: MeRoutesOptions = {}
) {
  const authService = options.authService ?? supabaseAuthService;
  const meService = options.meService ?? getMe;

  app.get(
    AppRoute.Me,
    {
      schema: meRouteSchema
    },
    async (request, reply) => {
      const token = extractBearerToken(request.headers.authorization);

      if (!token) {
        return reply.code(401).send(unauthorizedResponse);
      }

      try {
        const user = await authService.getUserFromToken(token);

        if (!user) {
          return reply.code(401).send(unauthorizedResponse);
        }

        return await meService(user);
      } catch (error) {
        request.log.error(error);

        return reply.code(500).send({
          status: "error"
        });
      }
    }
  );
}
