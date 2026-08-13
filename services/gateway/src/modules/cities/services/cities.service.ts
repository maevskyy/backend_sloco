import { mapCatalogCity } from "../common/cities.mappers.js";
import type {
  CitiesResult,
  CitiesServiceContract,
  CitiesStoreContract
} from "../common/cities.types.js";
import { CitiesStore } from "../stores/cities.store.js";

export class CitiesServiceImpl implements CitiesServiceContract {
  constructor(private readonly store: CitiesStoreContract) {}

  async listCities(): Promise<CitiesResult> {
    const rows = await this.store.listCatalogCities();
    return { cities: rows.map(mapCatalogCity) };
  }
}

export function createCitiesService(
  store: CitiesStoreContract = new CitiesStore()
): CitiesServiceContract {
  return new CitiesServiceImpl(store);
}

export const citiesService = createCitiesService();
