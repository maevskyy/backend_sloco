import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import { ZodError } from "zod";
import {
  LogEvent,
  LogEventType,
  LogMessagePrefix
} from "../../config/log-events.js";
import { AppRoute, VersionedAppRoute } from "../../config/routes.js";
import {
  extractBearerToken,
  supabaseAuthService,
  type AuthService,
  type AuthenticatedUser
} from "../auth/auth.service.js";
import {
  addPlaceToCollectionRouteSchema,
  createCollectionRouteSchema,
  deleteCollectionRouteSchema,
  getSavedCollectionRouteSchema,
  getSavedDashboardRouteSchema,
  removePlaceFromCollectionRouteSchema,
  reorderCollectionPlacesRouteSchema,
  savePlaceRouteSchema,
  unsavePlaceRouteSchema,
  updateCollectionRouteSchema
} from "./saved-places.openapi.js";
import {
  addPlaceToCollectionBodySchema,
  createSavedCollectionBodySchema,
  reorderCollectionPlacesBodySchema,
  savePlaceBodySchema,
  savedCollectionParamsSchema,
  savedCollectionPlaceParamsSchema,
  savedPlaceParamsSchema,
  updateSavedCollectionBodySchema
} from "./saved-places.schemas.js";
import {
  CollectionPlacesOrderError,
  DefaultSavedCollectionDeleteError,
  PlaceNotFoundError,
  savedPlacesService,
  SavedCollectionNotFoundError,
  type SavedPlacesService
} from "./saved-places.service.js";

type SavedPlacesRoutesOptions = {
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

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

export async function registerSavedPlacesRoutes(
  app: FastifyInstance,
  options: SavedPlacesRoutesOptions = {}
) {
  const authService = options.authService ?? supabaseAuthService;
  const service = options.savedPlacesService ?? savedPlacesService;

  app.get(
    AppRoute.MeSaved,
    {
      schema: getSavedDashboardRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const result = await service.getSavedDashboard(user.id);

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
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.get(
    AppRoute.MeSavedCollection,
    {
      schema: getSavedCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId } = savedCollectionParamsSchema.parse(
          request.params
        );

        return await service.getCollectionDetail(user.id, collectionId);
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.post(
    AppRoute.MeSavedPlaces,
    {
      schema: savePlaceRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const body = savePlaceBodySchema.parse(request.body);
        const result = await service.savePlace(user.id, body);

        logSavedPlaceResponse(request, VersionedAppRoute.meSavedPlaces, {
          placeId: result.placeId,
          isSaved: true,
          collectionCount: result.collectionIds.length
        });

        return result;
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.delete(
    AppRoute.MeSavedPlace,
    {
      schema: unsavePlaceRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { placeId } = savedPlaceParamsSchema.parse(request.params);
        const result = await service.unsavePlace(user.id, placeId);

        logSavedPlaceResponse(request, VersionedAppRoute.meSavedPlace, {
          placeId,
          isSaved: false,
          collectionCount: 0
        });

        return result;
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.post(
    AppRoute.MeSavedCollections,
    {
      schema: createCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const body = createSavedCollectionBodySchema.parse(request.body);

        return await service.createCollection(user.id, body);
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.patch(
    AppRoute.MeSavedCollection,
    {
      schema: updateCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId } = savedCollectionParamsSchema.parse(
          request.params
        );
        const body = updateSavedCollectionBodySchema.parse(request.body);

        return await service.updateCollection(user.id, collectionId, body);
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.delete(
    AppRoute.MeSavedCollection,
    {
      schema: deleteCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId } = savedCollectionParamsSchema.parse(
          request.params
        );

        return await service.deleteCollection(user.id, collectionId);
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.post(
    AppRoute.MeSavedCollectionPlaces,
    {
      schema: addPlaceToCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId } = savedCollectionParamsSchema.parse(
          request.params
        );
        const { placeId } = addPlaceToCollectionBodySchema.parse(request.body);

        return await service.addPlaceToCollection(user.id, collectionId, placeId);
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.delete(
    AppRoute.MeSavedCollectionPlace,
    {
      schema: removePlaceFromCollectionRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId, placeId } = savedCollectionPlaceParamsSchema.parse(
          request.params
        );

        return await service.removePlaceFromCollection(
          user.id,
          collectionId,
          placeId
        );
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );

  app.patch(
    AppRoute.MeSavedCollectionPlacesOrder,
    {
      schema: reorderCollectionPlacesRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      const user = await requireAuthenticatedUser(request, reply, authService);

      if (!user) {
        return reply;
      }

      try {
        const { collectionId } = savedCollectionParamsSchema.parse(
          request.params
        );
        const { placeIds } = reorderCollectionPlacesBodySchema.parse(
          request.body
        );

        return await service.reorderCollectionPlaces(
          user.id,
          collectionId,
          placeIds
        );
      } catch (error) {
        return handleSavedPlacesError(request, reply, error);
      }
    }
  );
}

async function requireAuthenticatedUser(
  request: FastifyRequest,
  reply: FastifyReply,
  authService: AuthService
): Promise<AuthenticatedUser | null> {
  const token = extractBearerToken(request.headers.authorization);

  if (!token) {
    reply.code(401).send(unauthorizedResponse);
    return null;
  }

  const user = await authService.getUserFromToken(token);

  if (!user) {
    reply.code(401).send(unauthorizedResponse);
    return null;
  }

  return user;
}

function handleSavedPlacesError(
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

  return reply.code(500).send({
    status: "error"
  });
}

function logSavedPlaceResponse(
  request: FastifyRequest,
  path: string,
  details: {
    placeId: number;
    isSaved: boolean;
    collectionCount: number;
  }
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
