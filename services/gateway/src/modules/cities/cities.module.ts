import type { FastifyInstance } from "fastify";
import { CitiesController } from "./controllers/cities.controller.js";
import { citiesService } from "./services/cities.service.js";
import type { CitiesServiceContract } from "./common/cities.types.js";

export type CitiesModuleOptions = {
  citiesService?: CitiesServiceContract;
};

export async function registerCitiesModule(
  app: FastifyInstance,
  options: CitiesModuleOptions = {}
) {
  const controller = new CitiesController(
    options.citiesService ?? citiesService
  );

  controller.register(app);
}
