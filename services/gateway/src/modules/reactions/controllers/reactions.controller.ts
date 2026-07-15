import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { handleCommonError } from "../../../http/errors.js";
import { docsRoute } from "../../../http/route.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import { PlaceNotFoundError } from "../common/reactions.errors.js";
import * as openApi from "../common/reactions.openapi.js";
import * as schemas from "../common/reactions.schemas.js";
import type { ReactionsServiceContract } from "../common/reactions.types.js";

const placeNotFoundResponse = {
  status: "error",
  message: "Place not found"
} as const;

export class ReactionsController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: ReactionsServiceContract,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(
      AppRoute.MeReactions,
      docsRoute(openApi.getReactionsRouteSchema),
      this.getReactions.bind(this)
    );
    app.put(
      AppRoute.MePlaceReaction,
      docsRoute(openApi.setReactionRouteSchema),
      this.setReaction.bind(this)
    );
    app.delete(
      AppRoute.MePlaceReaction,
      docsRoute(openApi.deleteReactionRouteSchema),
      this.deleteReaction.bind(this)
    );
  }

  private async getReactions(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const result = await this.service.getReactions(user.id);

      logResponseSummary(
        request,
        VersionedAppRoute.meReactions,
        {
          favoritesCount: result.favorites.length,
          dislikesCount: result.dislikes.length,
          hiddenCount: result.hidden.length
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.meReactions} ${result.favorites.length} favorites`
      );

      return result;
    });
  }

  private async setReaction(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { placeId } = schemas.reactionParamsSchema.parse(request.params);
      const { reaction } = schemas.setReactionBodySchema.parse(request.body);
      const result = await this.service.setReaction(user.id, placeId, reaction);

      logResponseSummary(
        request,
        VersionedAppRoute.mePlaceReaction,
        {
          placeId,
          reaction
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mePlaceReaction} ${reaction} for place ${placeId}`
      );

      return result;
    });
  }

  private async deleteReaction(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { placeId } = schemas.reactionParamsSchema.parse(request.params);
      await this.service.deleteReaction(user.id, placeId);

      logResponseSummary(
        request,
        VersionedAppRoute.mePlaceReaction,
        {
          placeId,
          deleted: true
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.mePlaceReaction} deleted for place ${placeId}`
      );

      return reply.code(204).send();
    });
  }

  private async withUser<T>(
    request: FastifyRequest,
    reply: FastifyReply,
    handler: (user: AuthenticatedUser) => Promise<T>
  ) {
    const user = await this.authGuard.requireUser(request, reply);

    if (!user) {
      return reply;
    }

    try {
      return await handler(user);
    } catch (error) {
      return this.handleError(request, reply, error);
    }
  }

  private handleError(
    request: FastifyRequest,
    reply: FastifyReply,
    error: unknown
  ) {
    if (error instanceof PlaceNotFoundError) {
      return reply.code(404).send(placeNotFoundResponse);
    }

    return handleCommonError(request, reply, error, "Invalid reactions request");
  }
}
