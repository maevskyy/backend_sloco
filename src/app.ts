import cors from "@fastify/cors";
import Fastify from "fastify";
import { registerHealthRoutes } from "./modules/health/health.routes.js";

type AppOptions = {
  supabaseHealthCheck?: () => Promise<void>;
};

export async function buildApp(options: AppOptions = {}) {
  const app = Fastify({
    logger: true
  });

  await app.register(cors, {
    origin: true
  });

  await app.register(registerHealthRoutes, {
    supabaseHealthCheck: options.supabaseHealthCheck
  });

  return app;
}
