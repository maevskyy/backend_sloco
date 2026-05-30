import type { FastifyInstance } from "fastify";
import { ZodError } from "zod";
import {
  LogEvent,
  LogEventType,
  LogMessagePrefix
} from "../../config/log-events.js";
import { AppRoute, VersionedAppRoute } from "../../config/routes.js";
import { mapPlacesRouteSchema } from "./map.openapi.js";
import { mapPlacesQuerySchema } from "./map.schemas.js";
import { getMapPlaces, type MapPlacesService } from "./map.service.js";

type MapRoutesOptions = {
  mapPlacesService?: MapPlacesService;
};

export async function registerMapRoutes(
  app: FastifyInstance,
  options: MapRoutesOptions = {}
) {
  const mapPlacesService = options.mapPlacesService ?? getMapPlaces;

  app.get(
    AppRoute.MapPlaces,
    {
      schema: mapPlacesRouteSchema,
      validatorCompiler: () => () => true
    },
    async (request, reply) => {
      try {
        const query = mapPlacesQuerySchema.parse(request.query);
        const result = await mapPlacesService(query);

        request.log.info(
          {
            eventType: LogEventType.Response,
            event: LogEvent.ResponseSummary,
            path: VersionedAppRoute.mapPlaces,
            city: query.city,
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
