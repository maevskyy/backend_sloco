import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { CitiesService } from "../index.js";

const citiesService: CitiesService = {
  async listCities() {
    return {
      cities: [
        { name: "Bucharest", country: "Romania", placeCount: 8000 },
        { name: "Tbilisi", country: "Georgia", placeCount: 4500 }
      ]
    };
  }
};

describe("cities routes", () => {
  it("returns the catalog city list without auth", async () => {
    const app = await buildApp({ citiesService });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.cities
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      cities: [
        { name: "Bucharest", country: "Romania", placeCount: 8000 },
        { name: "Tbilisi", country: "Georgia", placeCount: 4500 }
      ]
    });
  });

  it("does not expose an unversioned cities route", async () => {
    const app = await buildApp({ citiesService });

    const response = await app.inject({
      method: "GET",
      url: AppRoute.Cities
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
