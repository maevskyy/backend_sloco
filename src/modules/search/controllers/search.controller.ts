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
import type { SavedPlacesService } from "../../saved-places/index.js";
import { searchPlacesRouteSchema } from "../common/search.openapi.js";
import { searchPlacesQuerySchema } from "../common/search.schemas.js";
import type { SearchPlacesService } from "../common/search.types.js";
import { enrichSearchSavedState } from "../services/search.service.js";

export class SearchController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly searchPlacesService: SearchPlacesService,
    private readonly savedPlacesService: SavedPlacesService,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.SearchPlaces,
      docsRoute(searchPlacesRouteSchema),
      this.getSearchPlaces.bind(this)
    );
  }

  private async getSearchPlaces(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    try {
      const user = await this.authGuard.optionalUser(request);

      if (user === "invalid") {
        return reply.code(401).send(unauthorizedResponse);
      }

      const query = searchPlacesQuerySchema.parse(request.query);
      const result = await enrichSearchSavedState(
        await this.searchPlacesService(query),
        user?.id,
        this.savedPlacesService
      );

      logResponseSummary(
        request,
        VersionedAppRoute.searchPlaces,
        {
          query: query.q,
          limit: query.limit,
          placesCount: result.places.length,
          hasLocation: query.lat !== undefined && query.lng !== undefined,
          city: query.city,
          country: query.country
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.searchPlaces} ${result.places.length} places`
      );

      return result;
    } catch (error) {
      return handleCommonError(
        request,
        reply,
        error,
        "Invalid search places query"
      );
    }
  }
}
