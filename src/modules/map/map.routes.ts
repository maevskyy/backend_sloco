import type { FastifyInstance } from "fastify";
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
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { mapPlacesRouteSchema } from "./map.openapi.js";
import { mapPlacesQuerySchema } from "./map.schemas.js";
import {
  getMapPlaces,
  type MapPlacePin,
  type MapPlacesResult,
  type MapPlacesService
} from "./map.service.js";

type MapRoutesOptions = {
  mapPlacesService?: MapPlacesService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

const unauthorizedResponse = {
  status: "error",
  message: "Unauthorized"
} as const;

export async function registerMapRoutes(
  app: FastifyInstance,
  options: MapRoutesOptions = {}
) {
  const mapPlacesService = options.mapPlacesService ?? getMapPlaces;
  const authService = options.authService ?? supabaseAuthService;
  const savedService = options.savedPlacesService ?? savedPlacesService;

  app.get(
    AppRoute.MapPlaces,
    {
      schema: mapPlacesRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      try {
        const user = await getOptionalAuthenticatedUser(
          request.headers.authorization,
          authService
        );

        if (user === "invalid") {
          return reply.code(401).send(unauthorizedResponse);
        }

        const query = mapPlacesQuerySchema.parse(request.query);
        const result = await enrichSavedState(
          await mapPlacesService(query),
          user?.id,
          savedService
        );

        request.log.info(
          {
            eventType: LogEventType.Response,
            event: LogEvent.ResponseSummary,
            path: VersionedAppRoute.mapPlaces,
            zoom: query.zoom,
            limit: query.limit,
            placesCount: result.places.length,
            bbox: {
              swLat: query.swLat,
              swLng: query.swLng,
              neLat: query.neLat,
              neLng: query.neLng
            }
          },
          `${LogMessagePrefix.Response} ${VersionedAppRoute.mapPlaces} ${result.places.length} places`
        );

        return result;
      } catch (error) {
        if (error instanceof ZodError) {
          return reply.code(400).send({
            status: "error",
            message: "Invalid map places query",
            issues: error.issues
          });
        }

        request.log.error(error);

        return reply.code(500).send({
          status: "error"
        });
      }
    }
  );
}

async function getOptionalAuthenticatedUser(
  authorization: unknown,
  authService: AuthService
): Promise<AuthenticatedUser | "invalid" | null> {
  if (authorization === undefined) {
    return null;
  }

  const token = extractBearerToken(authorization);

  if (!token) {
    return "invalid";
  }

  return (await authService.getUserFromToken(token)) ?? "invalid";
}

async function enrichSavedState(
  result: MapPlacesResult,
  userId: string | undefined,
  savedService: SavedPlacesService
): Promise<MapPlacesResult> {
  if (!userId || result.places.length === 0) {
    return {
      places: result.places.map(markPlaceAsUnsaved)
    };
  }

  const savedPlaceStates = await savedService.getSavedPlaceStates(
    userId,
    result.places.map((place) => place.id)
  );

  return {
    places: result.places.map((place) => ({
      ...place,
      isSaved: savedPlaceStates.get(place.id)?.isSaved ?? false,
      savedCollectionIds: savedPlaceStates.get(place.id)?.collectionIds ?? []
    }))
  };
}

function markPlaceAsUnsaved(place: MapPlacePin): MapPlacePin {
  return {
    ...place,
    isSaved: false,
    savedCollectionIds: []
  };
}
