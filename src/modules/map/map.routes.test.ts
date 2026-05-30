import { describe, expect, it } from "vitest";
import { buildApp } from "../../app.js";
import { AppRoute, VersionedAppRoute } from "../../config/routes.js";
import type { MapPlacesService } from "./map.service.js";

const validQuery =
  `${VersionedAppRoute.mapPlaces}?city=Berlin&swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700`;

describe("map routes", () => {
  it("returns map places for a valid bbox query", async () => {
    const app = await buildApp({
      mapPlacesService: async () => ({
        places: [
          {
            id: 1,
            source: "tripadvisor",
            sourceId: "d5529357",
            name: "Pane e Vino",
            country: "Germany",
            city: "Berlin",
            latitude: 52.552578,
            longitude: 13.352883,
            rating: 4,
            priceLevel: null,
            numberOfReviews: 17,
            rawCuisineStyle: null
          }
        ]
      })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      places: [
        {
          id: 1,
          source: "tripadvisor",
          sourceId: "d5529357",
          name: "Pane e Vino",
          country: "Germany",
          city: "Berlin",
          latitude: 52.552578,
          longitude: 13.352883,
          rating: 4,
          priceLevel: null,
          numberOfReviews: 17,
          rawCuisineStyle: null
        }
      ]
    });
  });

  it("returns 400 when required query params are missing", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.mapPlaces}?city=Berlin`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when bbox coordinates are invalid", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.mapPlaces}?city=Berlin&swLat=52.5600&swLng=13.3300&neLat=52.4800&neLng=13.4700`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("applies the default limit", async () => {
    let capturedLimit: number | null = null;
    const mapPlacesService: MapPlacesService = async (query) => {
      capturedLimit = query.limit;
      return {
        places: []
      };
    };
    const app = await buildApp({
      mapPlacesService
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(capturedLimit).toBe(100);
  });

  it("returns 400 when limit is over 200", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&limit=201`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 500 when the map places service fails", async () => {
    const app = await buildApp({
      mapPlacesService: async () => {
        throw new Error("Supabase query failed");
      }
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(500);
    expect(response.json()).toEqual({
      status: "error"
    });
  });

  it("does not expose unversioned map routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: AppRoute.MapPlaces
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
