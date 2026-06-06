import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import { createAuthGuard, type AuthGuard } from "../../../http/auth-guard.js";
import { docsRoute } from "../../../http/route.js";
import { handleCommonError } from "../../../http/errors.js";
import {
  logResponseSummary,
  LogMessagePrefix
} from "../../../http/response-log.js";
import {
  CollectionPlacesOrderError,
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  SavedCollectionNotFoundError
} from "../common/saved-places.errors.js";
import * as openApi from "../common/saved-places.openapi.js";
import * as schemas from "../common/saved-places.schemas.js";
import type { SavedPlacesServiceContract } from "../common/saved-places.types.js";

const placeNotFoundResponse = {
  status: "error",
  message: "Place not found"
} as const;

const collectionNotFoundResponse = {
  status: "error",
  message: "Saved collection not found"
} as const;

export class SavedPlacesController {
  private readonly authGuard: AuthGuard;

  constructor(
    private readonly service: SavedPlacesServiceContract,
    authService: AuthService
  ) {
    this.authGuard = createAuthGuard(authService);
  }

  register(app: FastifyInstance) {
    app.get(AppRoute.MeSaved, docsRoute(openApi.getSavedDashboardRouteSchema),
      this.getSavedDashboard.bind(this));
    app.get(AppRoute.MeSavedCollection,
      docsRoute(openApi.getSavedCollectionRouteSchema),
      this.getCollectionDetail.bind(this));
    app.post(AppRoute.MeSavedPlaces, docsRoute(openApi.savePlaceRouteSchema),
      this.savePlace.bind(this));
    app.delete(AppRoute.MeSavedPlace, docsRoute(openApi.unsavePlaceRouteSchema),
      this.unsavePlace.bind(this));
    app.post(AppRoute.MeSavedCollections,
      docsRoute(openApi.createCollectionRouteSchema),
      this.createCollection.bind(this));
    app.patch(AppRoute.MeSavedCollection,
      docsRoute(openApi.updateCollectionRouteSchema),
      this.updateCollection.bind(this));
    app.delete(AppRoute.MeSavedCollection,
      docsRoute(openApi.deleteCollectionRouteSchema),
      this.deleteCollection.bind(this));
    app.post(AppRoute.MeSavedCollectionPlaces,
      docsRoute(openApi.addPlaceToCollectionRouteSchema),
      this.addPlaceToCollection.bind(this));
    app.delete(AppRoute.MeSavedCollectionPlace,
      docsRoute(openApi.removePlaceFromCollectionRouteSchema),
      this.removePlaceFromCollection.bind(this));
    app.patch(AppRoute.MeSavedCollectionPlacesOrder,
      docsRoute(openApi.reorderCollectionPlacesRouteSchema),
      this.reorderCollectionPlaces.bind(this));
  }

  private async getSavedDashboard(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const result = await this.service.getSavedDashboard(user.id);

      logResponseSummary(
        request,
        VersionedAppRoute.meSaved,
        {
          savedPlaceCount: result.summary.savedPlaceCount,
          collectionCount: result.summary.collectionCount
        },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.meSaved} ${result.summary.savedPlaceCount} saved places`
      );

      return result;
    });
  }

  private async getCollectionDetail(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId } = schemas.savedCollectionParamsSchema.parse(
        request.params
      );

      return this.service.getCollectionDetail(user.id, collectionId);
    });
  }

  private async savePlace(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const body = schemas.savePlaceBodySchema.parse(request.body);
      const result = await this.service.savePlace(user.id, body);

      this.logSavedPlaceResponse(request, VersionedAppRoute.meSavedPlaces, {
        placeId: result.placeId,
        isSaved: true,
        collectionCount: result.collectionIds.length
      });

      return result;
    });
  }

  private async unsavePlace(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { placeId } = schemas.savedPlaceParamsSchema.parse(request.params);
      const result = await this.service.unsavePlace(user.id, placeId);

      this.logSavedPlaceResponse(request, VersionedAppRoute.meSavedPlace, {
        placeId,
        isSaved: false,
        collectionCount: 0
      });

      return result;
    });
  }

  private async createCollection(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const body = schemas.createSavedCollectionBodySchema.parse(request.body);

      return this.service.createCollection(user.id, body);
    });
  }

  private async updateCollection(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId } = schemas.savedCollectionParamsSchema.parse(
        request.params
      );
      const body = schemas.updateSavedCollectionBodySchema.parse(request.body);

      return this.service.updateCollection(user.id, collectionId, body);
    });
  }

  private async deleteCollection(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId } = schemas.savedCollectionParamsSchema.parse(
        request.params
      );

      return this.service.deleteCollection(user.id, collectionId);
    });
  }

  private async addPlaceToCollection(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId } = schemas.savedCollectionParamsSchema.parse(
        request.params
      );
      const { placeId } = schemas.addPlaceToCollectionBodySchema.parse(
        request.body
      );

      return this.service.addPlaceToCollection(user.id, collectionId, placeId);
    });
  }

  private async removePlaceFromCollection(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId, placeId } =
        schemas.savedCollectionPlaceParamsSchema.parse(request.params);

      return this.service.removePlaceFromCollection(user.id, collectionId, placeId);
    });
  }

  private async reorderCollectionPlaces(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    return this.withUser(request, reply, async (user) => {
      const { collectionId } = schemas.savedCollectionParamsSchema.parse(
        request.params
      );
      const { placeIds } = schemas.reorderCollectionPlacesBodySchema.parse(
        request.body
      );

      return this.service.reorderCollectionPlaces(user.id, collectionId, placeIds);
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

    if (error instanceof SavedCollectionNotFoundError) {
      return reply.code(404).send(collectionNotFoundResponse);
    }

    if (error instanceof DefaultSavedCollectionDeleteError) {
      return reply.code(409).send({
        status: "error",
        message: "Default saved collection cannot be deleted"
      });
    }

    if (error instanceof CollectionPlacesOrderError) {
      return reply.code(400).send({
        status: "error",
        message: "Invalid collection place order"
      });
    }

    return handleCommonError(
      request,
      reply,
      error,
      "Invalid saved places request"
    );
  }

  private logSavedPlaceResponse(
    request: FastifyRequest,
    path: string,
    details: { placeId: number; isSaved: boolean; collectionCount: number }
  ) {
    logResponseSummary(
      request,
      path,
      {
        placeId: details.placeId,
        isSaved: details.isSaved,
        collectionCount: details.collectionCount
      },
      `${LogMessagePrefix.Response} ${path} place ${details.placeId} isSaved=${details.isSaved}`
    );
  }
}
