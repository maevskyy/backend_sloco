import cors from "@fastify/cors";
import Fastify from "fastify";
import { env } from "./config/env.js";
import {
  createLoggerConfig,
  logRequestCompletion
} from "./config/logger.js";
import { registerSwaggerDocs } from "./config/swagger.js";
import { registerHealthRoutes } from "./modules/health/health.routes.js";
import { registerMapRoutes } from "./modules/map/map.routes.js";
import type { MapPlacesService } from "./modules/map/map.service.js";

type AppOptions = {
  supabaseHealthCheck?: () => Promise<void>;
  mapPlacesService?: MapPlacesService;
};

export async function buildApp(options: AppOptions = {}) {
  const loggerConfig = createLoggerConfig(env.NODE_ENV);
  const app = Fastify({
    ...loggerConfig
  });

  app.addHook("onResponse", async (request, reply) => {
    logRequestCompletion(request, reply);
  });

  await app.register(cors, {
    origin: true
  });

  await registerSwaggerDocs(app);

  await app.register(registerHealthRoutes, {
    prefix: "/v1",
    supabaseHealthCheck: options.supabaseHealthCheck
  });

  await app.register(registerMapRoutes, {
    prefix: "/v1",
    mapPlacesService: options.mapPlacesService
  });

  return app;
}
