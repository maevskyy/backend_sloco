import type { CatalogCity, CatalogCityRow } from "./cities.types.js";

export function mapCatalogCity(row: CatalogCityRow): CatalogCity {
  return {
    name: row.name,
    country: row.country,
    placeCount: row.place_count
  };
}
