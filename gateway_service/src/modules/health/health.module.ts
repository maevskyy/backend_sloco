import type { FastifyInstance } from "fastify";
import { HealthController } from "./controllers/health.controller.js";
import { createHealthService } from "./services/health.service.js";

export type HealthModuleOptions = {
  supabaseHealthCheck?: () => Promise<void>;
};

export async function registerHealthModule(
  app: FastifyInstance,
  options: HealthModuleOptions = {}
) {
  // A custom `supabaseHealthCheck` (used by tests) replaces the store's DB ping.
  const healthService = options.supabaseHealthCheck
    ? createHealthService({ checkConnection: options.supabaseHealthCheck })
    : createHealthService();

  const controller = new HealthController(healthService);

  controller.register(app);
}
