export {
  registerSearchModule,
  type SearchModuleOptions
} from "./search.module.js";
export {
  createSearchPlacesService,
  enrichSearchSavedState,
  getSearchPlaces
} from "./services/search.service.js";
export { SearchStore } from "./stores/search.store.js";
export { searchComponentSchemas } from "./common/search.openapi.js";
export type {
  SearchPlaceResult,
  SearchPlaceRow,
  SearchPlacesResult,
  SearchPlacesService,
  SearchStoreContract
} from "./common/search.types.js";
export type { SearchPlacesQuery } from "./common/search.schemas.js";
