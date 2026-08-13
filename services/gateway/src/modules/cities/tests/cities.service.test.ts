import { describe, expect, it } from "vitest";
import { createCitiesService } from "../services/cities.service.js";
import type { CitiesStoreContract } from "../common/cities.types.js";

describe("cities service", () => {
  it("maps catalog rows into the public list shape", async () => {
    const store: CitiesStoreContract = {
      async listCatalogCities() {
        return [
          { name: "Bucharest", country: "Romania", place_count: 8000 },
          { name: "Tbilisi", country: "Georgia", place_count: 4500 }
        ];
      }
    };

    const result = await createCitiesService(store).listCities();

    expect(result.cities).toEqual([
      { name: "Bucharest", country: "Romania", placeCount: 8000 },
      { name: "Tbilisi", country: "Georgia", placeCount: 4500 }
    ]);
  });

  it("returns an empty list when the catalog has no cities", async () => {
    const store: CitiesStoreContract = {
      async listCatalogCities() {
        return [];
      }
    };

    const result = await createCitiesService(store).listCities();

    expect(result.cities).toEqual([]);
  });
});
