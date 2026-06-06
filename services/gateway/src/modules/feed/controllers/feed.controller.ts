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
import { docsRoute } from "../../../http/route.js";
import type { AuthService } from "../../auth/auth.service.js";
import { feedPlacesRouteSchema } from "../common/feed.openapi.js";
import { feedPlacesQuerySchema } from "../common/feed.schemas.js";
import type { FeedPlacesService } from "../common/feed.types.js";

export class FeedController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly feedPlacesService: FeedPlacesService,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.FeedPlaces,
      docsRoute(feedPlacesRouteSchema),
      this.getFeedPlaces.bind(this)
    );
  }

  private async getFeedPlaces(request: FastifyRequest, reply: FastifyReply) {
    try {
      const user = await this.authGuard.optionalUser(request);

      if (user === "invalid") {
        return reply.code(401).send(unauthorizedResponse);
      }

      const query = feedPlacesQuerySchema.parse(request.query);
      const result = await this.feedPlacesService({
        query,
        user
      });

      logResponseSummary(
        request,
        VersionedAppRoute.feedPlaces,
        {
          limit: query.limit,
          placesCount: result.places.length,
          personalizationStatus: result.feed.personalizationStatus,
          cacheStatus: result.feed.cacheStatus,
          hasLocation: query.lat !== undefined && query.lng !== undefined,
          city: query.city,
          country: query.country,
          authenticated: user !== null
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.feedPlaces} ${result.places.length} places`
      );

      return result;
    } catch (error) {
      return handleCommonError(
        request,
        reply,
        error,
        "Invalid feed places query"
      );
    }
  }
}
