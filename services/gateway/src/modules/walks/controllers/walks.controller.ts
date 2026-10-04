import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { handleCommonError, unauthorizedResponse } from "../../../http/errors.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import {
  FixedWindowRateLimiter,
  type RateLimitRule
} from "../../../http/rate-limiter.js";
import { docsRoute } from "../../../http/route.js";
import {
  WalkPlannerBusyError,
  WalkPlannerTimeoutError,
  WalkPlannerUnavailableError
} from "../../../lib/walk-planner-client.js";
import type { AuthService } from "../../auth/auth.service.js";
import {
  walksConfigRouteSchema,
  walksInsertRouteSchema,
  walksPlaceRouteSchema,
  walksPlanRouteSchema,
  walksScheduleRouteSchema,
  walksSearchRouteSchema
} from "../common/walks.openapi.js";
import {
  walksBodySchema,
  walksConfigQuerySchema,
  walksPlaceParamsSchema,
  walksPlaceQuerySchema,
  walksRouteQuerySchema,
  walksSearchQuerySchema
} from "../common/walks.schemas.js";
import type {
  WalksCallContext,
  WalksReply,
  WalksServiceContract
} from "../common/walks.types.js";

// The container refuses connections ~5–10 s during a restart or bundle switch.
const UNAVAILABLE_RETRY_AFTER_SECONDS = "5";
// Same pause the service asks for with its own 503 busy.
const BUSY_RETRY_AFTER_SECONDS = "2";

export type WalkRateBucket = "plan" | "edit" | "search" | "place";

// Per user id, or per client IP for anonymous calls (INTEGRATION.md §3.6). A
// plan costs 0.4–5 s of a core; every drag or stepper change of an edit is a
// call (the app debounces); search is typed (debounced ~300 ms). Config is
// cached and unlimited.
export const WALK_RATE_LIMITS: Record<WalkRateBucket, RateLimitRule[]> = {
  plan: [
    { limit: 6, windowMs: 60_000 },
    { limit: 60, windowMs: 3_600_000 }
  ],
  edit: [{ limit: 60, windowMs: 60_000 }],
  search: [{ limit: 60, windowMs: 60_000 }],
  place: [{ limit: 120, windowMs: 60_000 }]
};

export type WalkRateLimiters = Record<
  WalkRateBucket,
  Pick<FixedWindowRateLimiter, "check">
>;

export function createWalkRateLimiters(
  limits: Record<WalkRateBucket, RateLimitRule[]> = WALK_RATE_LIMITS
): WalkRateLimiters {
  return {
    plan: new FixedWindowRateLimiter(limits.plan),
    edit: new FixedWindowRateLimiter(limits.edit),
    search: new FixedWindowRateLimiter(limits.search),
    place: new FixedWindowRateLimiter(limits.place)
  };
}

const GATEWAY_ERROR_TEXT = {
  walk_planner_unavailable: {
    ru: "Сервис маршрутов временно недоступен. Попробуйте ещё раз.",
    en: "The walk planner is temporarily unavailable. Try again."
  },
  walk_planner_timeout: {
    ru: "Сервис маршрутов не ответил вовремя. Попробуйте ещё раз.",
    en: "The walk planner did not answer in time. Try again."
  },
  busy: {
    ru: "Сервис маршрутов сейчас занят. Попробуйте через пару секунд.",
    en: "The walk planner is busy. Try again in a few seconds."
  },
  rate_limited: {
    ru: "Слишком много запросов. Попробуйте чуть позже.",
    en: "Too many requests. Try again a little later."
  }
} as const;

type GatewayErrorCode = keyof typeof GATEWAY_ERROR_TEXT;

// Request and response bodies are never logged: they hold the user's
// location, favourites and where they will be (INTEGRATION.md §3.10).
export class WalksController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: WalksServiceContract,
    authService: AuthService,
    private readonly limiters: WalkRateLimiters = createWalkRateLimiters()
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.WalksConfig,
      docsRoute(walksConfigRouteSchema),
      this.config.bind(this)
    );
    app.post(
      AppRoute.WalksPlan,
      docsRoute(walksPlanRouteSchema),
      this.plan.bind(this)
    );
    app.post(
      AppRoute.WalksSchedule,
      docsRoute(walksScheduleRouteSchema),
      this.schedule.bind(this)
    );
    app.post(
      AppRoute.WalksInsert,
      docsRoute(walksInsertRouteSchema),
      this.insert.bind(this)
    );
    app.get(
      AppRoute.WalksPlacesSearch,
      docsRoute(walksSearchRouteSchema),
      this.searchPlaces.bind(this)
    );
    app.get(
      AppRoute.WalksPlace,
      docsRoute(walksPlaceRouteSchema),
      this.place.bind(this)
    );
  }

  private config(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(request, reply, VersionedAppRoute.walksConfig, null, async () =>
      this.service.config(
        walksConfigQuerySchema.parse(request.query),
        context(request)
      )
    );
  }

  private plan(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(
      request,
      reply,
      VersionedAppRoute.walksPlan,
      "plan",
      async (userId) =>
        this.service.plan(
          {
            userId,
            body: walksBodySchema.parse(request.body),
            query: walksRouteQuerySchema.parse(request.query)
          },
          context(request)
        )
    );
  }

  private schedule(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(
      request,
      reply,
      VersionedAppRoute.walksSchedule,
      "edit",
      async () =>
        this.service.schedule(
          {
            body: walksBodySchema.parse(request.body),
            query: walksRouteQuerySchema.parse(request.query)
          },
          context(request)
        )
    );
  }

  private insert(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(request, reply, VersionedAppRoute.walksInsert, "edit", async () =>
      this.service.insert(
        {
          body: walksBodySchema.parse(request.body),
          query: walksRouteQuerySchema.parse(request.query)
        },
        context(request)
      )
    );
  }

  private searchPlaces(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(
      request,
      reply,
      VersionedAppRoute.walksPlacesSearch,
      "search",
      async () =>
        this.service.searchPlaces(
          walksSearchQuerySchema.parse(request.query),
          context(request)
        )
    );
  }

  private place(request: FastifyRequest, reply: FastifyReply) {
    return this.handle(request, reply, VersionedAppRoute.walksPlace, "place", async () =>
      this.service.place(
        {
          sourceId: walksPlaceParamsSchema.parse(request.params).sourceId,
          query: walksPlaceQuerySchema.parse(request.query)
        },
        context(request)
      )
    );
  }

  // Optional auth on every route; only the plan uses the user (favourites).
  private async handle(
    request: FastifyRequest,
    reply: FastifyReply,
    route: string,
    bucket: WalkRateBucket | null,
    call: (userId: string | null) => Promise<WalksReply>
  ) {
    const user = await this.authGuard.optionalUser(request);

    if (user === "invalid") {
      return reply.code(401).send(unauthorizedResponse);
    }

    reply.header("X-Request-Id", request.id);

    if (bucket) {
      const decision = this.limiters[bucket].check(
        user ? `user:${user.id}` : `ip:${clientIp(request)}`
      );

      if (!decision.allowed) {
        request.log.warn({ bucket }, "walks rate limit hit");
        reply.header("Retry-After", String(decision.retryAfterSeconds));
        return sendGatewayError(request, reply, 429, "rate_limited", {
          retry_after_s: decision.retryAfterSeconds
        });
      }
    }

    try {
      const result = await call(user?.id ?? null);

      logResponseSummary(
        request,
        route,
        summaryFields(result),
        `${LogMessagePrefix.Response} ${route} ${result.status}`
      );

      if (result.retryAfter) {
        reply.header("Retry-After", result.retryAfter);
      }

      return reply.code(result.status).send(result.body);
    } catch (error) {
      if (error instanceof WalkPlannerUnavailableError) {
        request.log.warn({ err: error }, "walk-planner unavailable");
        reply.header("Retry-After", UNAVAILABLE_RETRY_AFTER_SECONDS);
        return sendGatewayError(request, reply, 503, "walk_planner_unavailable");
      }

      if (error instanceof WalkPlannerBusyError) {
        request.log.warn({ err: error }, "walk-planner gateway queue full");
        reply.header("Retry-After", BUSY_RETRY_AFTER_SECONDS);
        return sendGatewayError(request, reply, 503, "busy");
      }

      if (error instanceof WalkPlannerTimeoutError) {
        request.log.warn({ err: error }, "walk-planner timed out");
        return sendGatewayError(request, reply, 504, "walk_planner_timeout");
      }

      return handleCommonError(request, reply, error, "Invalid walk request");
    }
  }
}

// Nginx sets X-Real-IP to the peer it saw (and overwrites a client's own); the
// gateway port is loopback-only, so the header cannot come from anyone else.
function clientIp(request: FastifyRequest) {
  const realIp = request.headers["x-real-ip"];
  return (typeof realIp === "string" && realIp) || request.ip;
}

function context(request: FastifyRequest): WalksCallContext {
  return { requestId: request.id };
}

// What the log may keep about a walk answer: its status and codes, no places.
function summaryFields(result: WalksReply) {
  const body = (result.body ?? {}) as {
    status?: unknown;
    variants?: unknown;
    personalization?: { mode?: unknown };
    error?: { code?: unknown };
  };

  return {
    walkStatus: result.status,
    planStatus: body.status,
    variantsCount: Array.isArray(body.variants) ? body.variants.length : undefined,
    personalizationMode: body.personalization?.mode,
    errorCode: body.error?.code
  };
}

function sendGatewayError(
  request: FastifyRequest,
  reply: FastifyReply,
  status: number,
  code: GatewayErrorCode,
  params: Record<string, unknown> = {}
) {
  const lang = requestLang(request);

  return reply.code(status).send({
    error: { code, message: GATEWAY_ERROR_TEXT[code][lang], params }
  });
}

// The service defaults to Russian; ?lang= wins over the body's lang.
function requestLang(request: FastifyRequest): "ru" | "en" {
  const queryLang = (request.query as { lang?: unknown } | undefined)?.lang;
  const bodyLang = (request.body as { lang?: unknown } | undefined)?.lang;
  const lang = queryLang ?? bodyLang;
  return lang === "en" ? "en" : "ru";
}
