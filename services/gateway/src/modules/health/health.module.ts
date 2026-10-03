import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { HealthController } from "./controllers/health.controller.js";
import { createHealthService } from "./services/health.service.js";
import { HealthStore } from "./stores/health.store.js";

export type HealthModuleOptions = {
  supabaseHealthCheck?: () => Promise<void>;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerHealthModule(
  app: FastifyInstance,
  options: HealthModuleOptions = {}
) {
  // A custom `supabaseHealthCheck` (used by tests) replaces the store's DB ping.
  const healthService = options.supabaseHealthCheck
    ? createHealthService({ checkConnection: options.supabaseHealthCheck })
    : createHealthService(new HealthStore(options.db));

  const controller = new HealthController(healthService);

  controller.register(app);
}
