import cors from "@fastify/cors";
import Fastify from "fastify";
import { env } from "./config/env.js";
import {
  createLoggerConfig,
  logRequestCompletion
} from "./config/logger.js";
import { API_PREFIX } from "./config/routes.js";
import { registerSwaggerDocs } from "./config/swagger.js";
import { registerHealthRoutes } from "./modules/health/health.routes.js";
import { registerMapRoutes } from "./modules/map/map.routes.js";
import { registerMeRoutes } from "./modules/me/me.routes.js";
import {
  registerSavedPlacesModule,
  type SavedPlacesService
} from "./modules/saved-places/index.js";
import type { AuthService } from "./modules/auth/auth.service.js";
import type { MapPlacesService } from "./modules/map/map.service.js";
import type { MeService } from "./modules/me/me.service.js";

type AppOptions = {
  supabaseHealthCheck?: () => Promise<void>;
  mapPlacesService?: MapPlacesService;
  authService?: AuthService;
  meService?: MeService;
  savedPlacesService?: SavedPlacesService;
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
    prefix: API_PREFIX,
    supabaseHealthCheck: options.supabaseHealthCheck
  });

  await app.register(registerMeRoutes, {
    prefix: API_PREFIX,
    authService: options.authService,
    meService: options.meService
  });

  await app.register(registerSavedPlacesModule, {
    prefix: API_PREFIX,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerMapRoutes, {
    prefix: API_PREFIX,
    mapPlacesService: options.mapPlacesService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  return app;
}
