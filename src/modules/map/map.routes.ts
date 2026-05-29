import type { FastifyInstance } from "fastify";
import { ZodError } from "zod";
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

  app.get("/map/places", async (request, reply) => {
    try {
      const query = mapPlacesQuerySchema.parse(request.query);
      return await mapPlacesService(query);
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
  });
}
