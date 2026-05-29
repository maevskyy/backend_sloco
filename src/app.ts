import cors from "@fastify/cors";
import Fastify from "fastify";
import { registerHealthRoutes } from "./modules/health/health.routes.js";
import { registerMapRoutes } from "./modules/map/map.routes.js";
import type { MapPlacesService } from "./modules/map/map.service.js";

type AppOptions = {
  supabaseHealthCheck?: () => Promise<void>;
  mapPlacesService?: MapPlacesService;
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

  await app.register(registerMapRoutes, {
    mapPlacesService: options.mapPlacesService
  });

  return app;
}
