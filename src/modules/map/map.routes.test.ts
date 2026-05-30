import { describe, expect, it } from "vitest";
import { buildApp } from "../../app.js";
import { AppRoute, VersionedAppRoute } from "../../config/routes.js";
import type { MapPlacesService } from "./map.service.js";

const validQuery =
  `${VersionedAppRoute.mapPlaces}?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700`;

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
      url: `${VersionedAppRoute.mapPlaces}?swLat=52.4800&swLng=13.3300`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("returns 400 when bbox coordinates are invalid", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${VersionedAppRoute.mapPlaces}?swLat=52.5600&swLng=13.3300&neLat=52.4800&neLng=13.4700`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("passes the parsed zoom to the service", async () => {
    let capturedZoom: number | undefined;
    const mapPlacesService: MapPlacesService = async (query) => {
      capturedZoom = query.zoom;
      return {
        places: []
      };
    };
    const app = await buildApp({
      mapPlacesService
    });

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&zoom=13`
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(capturedZoom).toBe(13);
  });

  it("returns 200 when zoom is omitted", async () => {
    const app = await buildApp({
      mapPlacesService: async () => ({ places: [] })
    });

    const response = await app.inject({
      method: "GET",
      url: validQuery
    });

    await app.close();

    expect(response.statusCode).toBe(200);
  });

  it("returns 400 when zoom is out of range", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: `${validQuery}&zoom=99`
    });

    await app.close();

    expect(response.statusCode).toBe(400);
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
