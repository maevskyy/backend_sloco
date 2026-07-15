import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import { handleCommonError, unauthorizedResponse } from "../../../http/errors.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import { docsRoute } from "../../../http/route.js";
import type { AuthService } from "../../auth/auth.service.js";
import type { ReactionsService } from "../../reactions/index.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import { getPlaceDetailsRouteSchema } from "../common/places.openapi.js";
import { placeDetailsParamsSchema } from "../common/places.schemas.js";
import type {
  PlaceDetailsResult,
  PlaceDetailsService
} from "../common/places.types.js";

const placeNotFoundResponse = {
  status: "error",
  message: "Place not found"
} as const;

export class PlacesController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly placeDetailsService: PlaceDetailsService,
    private readonly savedPlacesService: SavedPlacesService,
    private readonly reactionsService: ReactionsService,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.Place,
      docsRoute(getPlaceDetailsRouteSchema),
      this.getPlaceDetails.bind(this)
    );
  }

  private async getPlaceDetails(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    try {
      const user = await this.authGuard.optionalUser(request);

      if (user === "invalid") {
        return reply.code(401).send(unauthorizedResponse);
      }

      const { placeId } = placeDetailsParamsSchema.parse(request.params);
      const result = await this.placeDetailsService(placeId);

      if (!result) {
        return reply.code(404).send(placeNotFoundResponse);
      }

      const enriched = await this.enrichSavedState(result, user?.id);

      logResponseSummary(
        request,
        VersionedAppRoute.place,
        {
          placeId,
          isSaved: enriched.place.isSaved
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.place} place ${placeId}`
      );

      return enriched;
    } catch (error) {
      return handleCommonError(
        request,
        reply,
        error,
        "Invalid place details request"
      );
    }
  }

  private async enrichSavedState(
    result: PlaceDetailsResult,
    userId: string | undefined
  ): Promise<PlaceDetailsResult> {
    if (!userId) {
      return result;
    }

    const savedState = (
      await this.savedPlacesService.getSavedPlaceStates(userId, [result.place.id])
    ).get(result.place.id);
    const reaction = (
      await this.reactionsService.getReactionMap(userId, [result.place.id])
    ).get(result.place.id);

    return {
      place: {
        ...result.place,
        isSaved: savedState?.isSaved ?? false,
        savedCollectionIds: savedState?.collectionIds ?? [],
        reaction: reaction ?? null
      }
    };
  }
}
