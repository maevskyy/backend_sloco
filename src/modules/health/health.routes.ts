import type { FastifyInstance } from "fastify";
import { checkSupabaseConnection } from "../../lib/supabase.js";
import {
  healthRouteSchema,
  supabaseHealthRouteSchema
} from "./health.openapi.js";

type HealthRoutesOptions = {
  supabaseHealthCheck?: () => Promise<void>;
};

export async function registerHealthRoutes(
  app: FastifyInstance,
  options: HealthRoutesOptions = {}
) {
  const supabaseHealthCheck =
    options.supabaseHealthCheck ?? checkSupabaseConnection;

  app.get(
    "/health",
    {
      schema: healthRouteSchema
    },
    async () => ({
      status: "ok"
    })
  );

  app.get(
    "/health/supabase",
    {
      schema: supabaseHealthRouteSchema
    },
    async (request, reply) => {
      try {
        await supabaseHealthCheck();

        return {
          status: "ok"
        };
      } catch (error) {
        request.log.error(error);

        return reply.code(500).send({
          status: "error"
        });
      }
    }
  );
}
