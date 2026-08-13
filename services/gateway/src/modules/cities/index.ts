export {
  registerCitiesModule,
  type CitiesModuleOptions
} from "./cities.module.js";
export {
  createCitiesService,
  citiesService,
  CitiesServiceImpl
} from "./services/cities.service.js";
export { CitiesStore } from "./stores/cities.store.js";
export { citiesComponentSchemas } from "./common/cities.openapi.js";
export type {
  CatalogCity,
  CitiesResult,
  CitiesServiceContract as CitiesService,
  CitiesServiceContract,
  CitiesStoreContract
} from "./common/cities.types.js";
