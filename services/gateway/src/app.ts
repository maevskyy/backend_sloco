import cors from "@fastify/cors";
import Fastify from "fastify";
import { env } from "./config/env.js";
import {
  createLoggerConfig,
  logRequestCompletion
} from "./config/logger.js";
import { API_PREFIX } from "./config/routes.js";
import { registerSwaggerDocs } from "./config/swagger.js";
import {
  enterRequestMetricContext,
  logHttpRequestMetric
} from "./observability/metrics.js";
import {
  metricsContentType,
  renderMetrics
} from "./observability/prometheus.js";
import { registerHealthModule } from "./modules/health/index.js";
import {
  registerMapModule,
  type MapPlacesService,
  type MapTileService
} from "./modules/map/index.js";
import { registerMeModule, type MeService } from "./modules/me/index.js";
import {
  registerPlacesModule,
  type PlaceDetailsService
} from "./modules/places/index.js";
import {
  registerReactionsModule,
  type ReactionsService
} from "./modules/reactions/index.js";
import {
  registerSavedPlacesModule,
  type SavedPlacesService
} from "./modules/saved-places/index.js";
import {
  registerSearchModule,
  type SearchPlacesService
} from "./modules/search/index.js";
import {
  registerFeedModule,
  type FeedPlacesService
} from "./modules/feed/index.js";
import type { AuthService } from "./modules/auth/auth.service.js";
import type { CacheStore } from "./lib/cache/cache-store.js";

type AppOptions = {
  supabaseHealthCheck?: () => Promise<void>;
  mapPlacesService?: MapPlacesService;
  mapTileService?: MapTileService;
  authService?: AuthService;
  meService?: MeService;
  reactionsService?: ReactionsService;
  savedPlacesService?: SavedPlacesService;
  placeDetailsService?: PlaceDetailsService;
  cacheStore?: CacheStore;
  searchPlacesService?: SearchPlacesService;
  feedPlacesService?: FeedPlacesService;
};

export async function buildApp(options: AppOptions = {}) {
  const loggerConfig = createLoggerConfig(env.NODE_ENV);
  const app = Fastify({
    ...loggerConfig
  });

  app.addHook("onRequest", async (request) => {
    enterRequestMetricContext(request);
  });

  app.addHook("onResponse", async (request, reply) => {
    logHttpRequestMetric(request, reply);
    logRequestCompletion(request, reply);
  });

  await app.register(cors, {
    origin: true
  });

  // Prometheus scrape endpoint. Not under /v1 → Nginx (which proxies only /v1/)
  // does not expose it publicly; Prometheus scrapes it on the private network.
  app.get("/metrics", async (_request, reply) => {
    reply.header("Content-Type", metricsContentType);
    return renderMetrics();
  });

  await registerSwaggerDocs(app);

  await app.register(registerHealthModule, {
    prefix: API_PREFIX,
    supabaseHealthCheck: options.supabaseHealthCheck
  });

  await app.register(registerMeModule, {
    prefix: API_PREFIX,
    authService: options.authService,
    meService: options.meService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerReactionsModule, {
    prefix: API_PREFIX,
    authService: options.authService,
    reactionsService: options.reactionsService
  });

  await app.register(registerSavedPlacesModule, {
    prefix: API_PREFIX,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerPlacesModule, {
    prefix: API_PREFIX,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService,
    placeDetailsService: options.placeDetailsService,
    cacheStore: options.cacheStore
  });

  await app.register(registerSearchModule, {
    prefix: API_PREFIX,
    searchPlacesService: options.searchPlacesService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerFeedModule, {
    prefix: API_PREFIX,
    feedPlacesService: options.feedPlacesService,
    authService: options.authService
  });

  await app.register(registerMapModule, {
    prefix: API_PREFIX,
    mapPlacesService: options.mapPlacesService,
    mapTileService: options.mapTileService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  return app;
}
