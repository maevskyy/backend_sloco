import type { FastifyInstance } from "fastify";
import { checkSupabaseConnection } from "../../lib/supabase.js";

type HealthRoutesOptions = {
  supabaseHealthCheck?: () => Promise<void>;
};

export async function registerHealthRoutes(
  app: FastifyInstance,
  options: HealthRoutesOptions = {}
) {
  const supabaseHealthCheck =
    options.supabaseHealthCheck ?? checkSupabaseConnection;

  app.get("/health", async () => ({
    status: "ok"
  }));

  app.get("/health/supabase", async (request, reply) => {
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
  });
}
