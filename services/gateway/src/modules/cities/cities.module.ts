import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { CitiesController } from "./controllers/cities.controller.js";
import { createCitiesService } from "./services/cities.service.js";
import { CitiesStore } from "./stores/cities.store.js";
import type { CitiesServiceContract } from "./common/cities.types.js";

export type CitiesModuleOptions = {
  citiesService?: CitiesServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerCitiesModule(
  app: FastifyInstance,
  options: CitiesModuleOptions = {}
) {
  const controller = new CitiesController(
    options.citiesService ?? createCitiesService(new CitiesStore(options.db))
  );

  controller.register(app);
}
