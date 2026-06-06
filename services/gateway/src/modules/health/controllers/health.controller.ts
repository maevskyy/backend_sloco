import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute } from "../../../config/routes.js";
import { docsRoute } from "../../../http/route.js";
import { handleCommonError } from "../../../http/errors.js";
import {
  healthRouteSchema,
  supabaseHealthRouteSchema
} from "../common/health.openapi.js";
import type { HealthService } from "../common/health.types.js";

export class HealthController {
  constructor(private readonly healthService: HealthService) {}

  register(app: FastifyInstance) {
    app.get(AppRoute.Health, docsRoute(healthRouteSchema), async () => ({
      status: "ok"
    }));
    app.get(
      AppRoute.SupabaseHealth,
      docsRoute(supabaseHealthRouteSchema),
      this.getSupabaseHealth.bind(this)
    );
  }

  private async getSupabaseHealth(request: FastifyRequest, reply: FastifyReply) {
    try {
      await this.healthService.checkSupabase();

      return { status: "ok" };
    } catch (error) {
      return handleCommonError(request, reply, error);
    }
  }
}
