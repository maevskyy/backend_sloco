import cors from "@fastify/cors";
import Fastify from "fastify";
import { env } from "./config/env.js";
import {
  createLoggerConfig,
  logRequestCompletion
} from "./config/logger.js";
import { API_PREFIX } from "./config/routes.js";
import { registerSwaggerDocs } from "./config/swagger.js";
import { sendDbUnavailable } from "./http/errors.js";
import { closeDb, getDb, onDbPoolError, type Db } from "./lib/db.js";
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
  registerOnboardingModule,
  type OnboardingService
} from "./modules/onboarding/index.js";
import {
  registerPlacesModule,
  type PlaceDetailsService
} from "./modules/places/index.js";
import {
  registerReactionsModule,
  type ReactionsService
} from "./modules/reactions/index.js";
import {
  registerReviewsModule,
  type ReviewsService
} from "./modules/reviews/index.js";
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
import {
  registerCitiesModule,
  type CitiesService
} from "./modules/cities/index.js";
import {
  registerEventsModule,
  type EventsServiceContract
} from "./modules/events/index.js";
import {
  registerWalksModule,
  type WalkRateLimiters,
  type WalksService
} from "./modules/walks/index.js";
import type { AuthService } from "./modules/auth/auth.service.js";
import type { CacheStore } from "./lib/cache/cache-store.js";

type AppOptions = {
  db?: Db;
  supabaseHealthCheck?: () => Promise<void>;
  mapPlacesService?: MapPlacesService;
  mapTileService?: MapTileService;
  authService?: AuthService;
  meService?: MeService;
  onboardingService?: OnboardingService;
  reactionsService?: ReactionsService;
  reviewsService?: ReviewsService;
  savedPlacesService?: SavedPlacesService;
  placeDetailsService?: PlaceDetailsService;
  cacheStore?: CacheStore;
  searchPlacesService?: SearchPlacesService;
  feedPlacesService?: FeedPlacesService;
  citiesService?: CitiesService;
  eventsService?: EventsServiceContract;
  walksService?: WalksService;
  walkRateLimiters?: WalkRateLimiters;
};

export async function buildApp(options: AppOptions = {}) {
  const loggerConfig = createLoggerConfig(env.NODE_ENV);
  const app = Fastify({
    ...loggerConfig
  });

  // One direct-Postgres pool per process (SLO-49), handed to every module.
  // getDb() is the same instance module-level default singletons resolve to,
  // so no second pool can appear; it connects lazily on the first query.
  const db = options.db ?? getDb();

  if (!options.db) {
    onDbPoolError((error) => {
      app.log.error({ err: error }, "postgres pool idle client error");
    });
    app.addHook("onClose", async () => {
      await closeDb();
    });
  }

  // Errors that escape a controller's try/catch: database saturation still
  // answers 503 + Retry-After; everything else goes to Fastify's default
  // handler unchanged. Set before modules register so they inherit it.
  app.setErrorHandler((error, request, reply) => {
    if (sendDbUnavailable(request, reply, error)) {
      return;
    }

    reply.send(error);
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
    db,
    supabaseHealthCheck: options.supabaseHealthCheck
  });

  await app.register(registerMeModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    meService: options.meService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerOnboardingModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    onboardingService: options.onboardingService
  });

  await app.register(registerReactionsModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    reactionsService: options.reactionsService
  });

  await app.register(registerReviewsModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    reviewsService: options.reviewsService
  });

  await app.register(registerSavedPlacesModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerPlacesModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService,
    reactionsService: options.reactionsService,
    placeDetailsService: options.placeDetailsService,
    cacheStore: options.cacheStore
  });

  await app.register(registerSearchModule, {
    prefix: API_PREFIX,
    db,
    searchPlacesService: options.searchPlacesService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  await app.register(registerFeedModule, {
    prefix: API_PREFIX,
    db,
    feedPlacesService: options.feedPlacesService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService,
    reactionsService: options.reactionsService
  });

  await app.register(registerCitiesModule, {
    prefix: API_PREFIX,
    db,
    citiesService: options.citiesService
  });

  await app.register(registerEventsModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    eventsService: options.eventsService
  });

  await app.register(registerWalksModule, {
    prefix: API_PREFIX,
    db,
    authService: options.authService,
    walksService: options.walksService,
    cacheStore: options.cacheStore,
    walkRateLimiters: options.walkRateLimiters
  });

  await app.register(registerMapModule, {
    prefix: API_PREFIX,
    db,
    mapPlacesService: options.mapPlacesService,
    mapTileService: options.mapTileService,
    authService: options.authService,
    savedPlacesService: options.savedPlacesService
  });

  return app;
}
