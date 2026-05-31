import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest,
  FastifySchema
} from "fastify";
import { ZodError } from "zod";
import {
  LogEvent,
  LogEventType,
  LogMessagePrefix
} from "../../../config/log-events.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import {
  extractBearerToken,
  type AuthService,
  type AuthenticatedUser
} from "../../auth/auth.service.js";
import {
  CollectionPlacesOrderError,
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  SavedCollectionNotFoundError
} from "../common/saved-places.errors.js";
import * as openApi from "../common/saved-places.openapi.js";
import * as schemas from "../common/saved-places.schemas.js";
import type { SavedPlacesServiceContract } from "../common/saved-places.types.js";

const unauthorizedResponse = {
  status: "error",
  message: "Unauthorized"
} as const;

const placeNotFoundResponse = {
  status: "error",
  message: "Place not found"
} as const;

const collectionNotFoundResponse = {
  status: "error",
  message: "Saved collection not found"
} as const;

export class SavedPlacesController {
  constructor(
    private readonly service: SavedPlacesServiceContract,
    private readonly authService: AuthService
  ) {}

  register(app: FastifyInstance) {
    app.get(AppRoute.MeSaved, this.route(openApi.getSavedDashboardRouteSchema),
      this.getSavedDashboard.bind(this));
    app.get(AppRoute.MeSavedCollection,
      this.route(openApi.getSavedCollectionRouteSchema),
      this.getCollectionDetail.bind(this));
    app.post(AppRoute.MeSavedPlaces, this.route(openApi.savePlaceRouteSchema),
      this.savePlace.bind(this));
    app.delete(AppRoute.MeSavedPlace, this.route(openApi.unsavePlaceRouteSchema),
      this.unsavePlace.bind(this));
    app.post(AppRoute.MeSavedCollections,
      this.route(openApi.createCollectionRouteSchema),
      this.createCollection.bind(this));
    app.patch(AppRoute.MeSavedCollection,
      this.route(openApi.updateCollectionRouteSchema),
      this.updateCollection.bind(this));
    app.delete(AppRoute.MeSavedCollection,
      this.route(openApi.deleteCollectionRouteSchema),
      this.deleteCollection.bind(this));
    app.post(AppRoute.MeSavedCollectionPlaces,
      this.route(openApi.addPlaceToCollectionRouteSchema),
      this.addPlaceToCollection.bind(this));
    app.delete(AppRoute.MeSavedCollectionPlace,
      this.route(openApi.removePlaceFromCollectionRouteSchema),
      this.removePlaceFromCollection.bind(this));
    app.patch(AppRoute.MeSavedCollectionPlacesOrder,
      this.route(openApi.reorderCollectionPlacesRouteSchema),
      this.reorderCollectionPlaces.bind(this));
  }

  private route(schema: FastifySchema) {
    return {
      schema,
      validatorCompiler: () => () => true
    };
  }

  private async getSavedDashboard(request: FastifyRequest, reply: FastifyReply) {
    return this.withUser(request, reply, async (user) => {
      const result = await this.service.getSavedDashboard(user.id);

      request.log.info(
        {
          eventType: LogEventType.Response,
          event: LogEvent.ResponseSummary,
          path: VersionedAppRoute.meSaved,
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
    const user = await this.requireAuthenticatedUser(request, reply);

    if (!user) {
      return reply;
    }

    try {
      return await handler(user);
    } catch (error) {
      return this.handleError(request, reply, error);
    }
  }

  private async requireAuthenticatedUser(
    request: FastifyRequest,
    reply: FastifyReply
  ) {
    const token = extractBearerToken(request.headers.authorization);

    if (!token) {
      reply.code(401).send(unauthorizedResponse);
      return null;
    }

    const user = await this.authService.getUserFromToken(token);

    if (!user) {
      reply.code(401).send(unauthorizedResponse);
      return null;
    }

    return user;
  }

  private handleError(
    request: FastifyRequest,
    reply: FastifyReply,
    error: unknown
  ) {
    if (error instanceof ZodError) {
      return reply.code(400).send({
        status: "error",
        message: "Invalid saved places request",
        issues: error.issues
      });
    }

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

    request.log.error(error);

    return reply.code(500).send({ status: "error" });
  }

  private logSavedPlaceResponse(
    request: FastifyRequest,
    path: string,
    details: { placeId: number; isSaved: boolean; collectionCount: number }
  ) {
    request.log.info(
      {
        eventType: LogEventType.Response,
        event: LogEvent.ResponseSummary,
        path,
        placeId: details.placeId,
        isSaved: details.isSaved,
        collectionCount: details.collectionCount
      },
      `${LogMessagePrefix.Response} ${path} place ${details.placeId} isSaved=${details.isSaved}`
    );
  }
}
