import type {
  FastifyError,
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
import { docsRoute } from "../../../http/route.js";
import type { AuthService } from "../../auth/auth.service.js";
import { eventsIngestRouteSchema } from "../common/events.openapi.js";
import {
  eventsBatchBodySchema,
  MAX_BATCH_BODY_BYTES,
  MAX_EVENTS_PER_BATCH
} from "../common/events.schemas.js";
import type { EventsServiceContract } from "../common/events.types.js";

const batchTooLargeResponse = {
  status: "error",
  message: `Batch over the limit (${MAX_EVENTS_PER_BATCH} events, 1 MiB body)`
} as const;

export class EventsController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: EventsServiceContract,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.post(AppRoute.Events, {
      ...docsRoute(eventsIngestRouteSchema),
      bodyLimit: MAX_BATCH_BODY_BYTES,
      // The spec pins 429 for an oversized batch; Fastify's native body-limit
      // error is 413, so remap just that one here.
      errorHandler: (error: FastifyError, request, reply) => {
        if (error.code === "FST_ERR_CTP_BODY_TOO_LARGE") {
          return reply.code(429).send(batchTooLargeResponse);
        }
        return handleCommonError(request, reply, error);
      },
      handler: this.ingestBatch.bind(this)
    });
  }

  private async ingestBatch(request: FastifyRequest, reply: FastifyReply) {
    // Optional auth: anonymous batches are valid (anon_id only); a present but
    // invalid token is still a 401 so a broken session cannot silently write
    // anonymous history.
    const user = await this.authGuard.optionalUser(request);

    if (user === "invalid") {
      return reply.code(401).send(unauthorizedResponse);
    }

    try {
      const body = eventsBatchBodySchema.parse(request.body);

      if (body.events.length > MAX_EVENTS_PER_BATCH) {
        return reply.code(429).send(batchTooLargeResponse);
      }

      const result = await this.service.ingestBatch({ body, user });

      logResponseSummary(
        request,
        VersionedAppRoute.events,
        {
          batchId: body.batch_id,
          accepted: result.accepted,
          duplicates: result.duplicates,
          rejectedCount: result.rejected.length,
          authenticated: user !== null
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.events} accepted=${result.accepted} duplicates=${result.duplicates} rejected=${result.rejected.length}`
      );

      return reply.code(202).send(result);
    } catch (error) {
      return handleCommonError(request, reply, error, "Invalid events batch");
    }
  }
}
