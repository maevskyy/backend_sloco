import { getPgPool } from "../../../lib/pg.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { CatalogCityRow, CitiesStoreContract } from "../common/cities.types.js";

export class CitiesStore implements CitiesStoreContract {
  async listCatalogCities(): Promise<CatalogCityRow[]> {
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "select",
        name: "catalog_cities"
      },
      async () =>
        getPgPool().query<CatalogCityRow>(
          `select
             p.city as name,
             p.country,
             count(*)::int as place_count
           from public.places p
           where p.city is not null
             and btrim(p.city) <> ''
           group by p.city, p.country
           order by place_count desc, p.city asc`
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }
}
