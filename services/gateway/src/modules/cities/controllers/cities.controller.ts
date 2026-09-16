import type {
  FastifyInstance,
  FastifyReply,
  FastifyRequest
} from "fastify";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import { handleCommonError } from "../../../http/errors.js";
import {
  LogMessagePrefix,
  logResponseSummary
} from "../../../http/response-log.js";
import { docsRoute } from "../../../http/route.js";
import { listCitiesRouteSchema } from "../common/cities.openapi.js";
import type { CitiesServiceContract } from "../common/cities.types.js";

export class CitiesController {
  constructor(private readonly citiesService: CitiesServiceContract) {}

  register(app: FastifyInstance) {
    app.get(
      AppRoute.Cities,
      docsRoute(listCitiesRouteSchema),
      this.listCities.bind(this)
    );
  }

  private async listCities(request: FastifyRequest, reply: FastifyReply) {
    try {
      const result = await this.citiesService.listCities();

      logResponseSummary(
        request,
        VersionedAppRoute.cities,
        { citiesCount: result.cities.length },
        `${LogMessagePrefix.Response} ${VersionedAppRoute.cities} ${result.cities.length} cities`
      );

      return result;
    } catch (error) {
      return handleCommonError(request, reply, error);
    }
  }
}
