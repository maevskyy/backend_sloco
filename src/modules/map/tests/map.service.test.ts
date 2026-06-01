import { describe, expect, it } from "vitest";
import { createMapPlacesService } from "../services/map.service.js";
import type { MapStoreContract, PlaceRow } from "../common/map.types.js";

function place(overrides: Partial<PlaceRow>): PlaceRow {
  return {
    id: 1,
    source: "tripadvisor",
    source_id: "source-1",
    name: "Place",
    category: "cafe",
    primary_type: "cafe",
    latitude: 52.5,
    longitude: 13.4,
    rating: 4,
    price_level: null,
    reviews_count: 10,
    google_rating: 4,
    google_user_rating_count: 10,
    rating_score_0_100: null,
    popularity_score_0_100: null,
    map_visibility_score: null,
    map_visibility_rank: null,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    ...overrides
  };
}

describe("map places service", () => {
  it("marks top ranked places as featured and remaining places as dots", async () => {
    const rows = Array.from({ length: 14 }, (_, index) =>
      place({
        id: index + 1,
        source_id: `source-${index + 1}`,
        rating: 5 - index * 0.01,
        reviews_count: 100 - index
      })
    );
    const store: MapStoreContract = {
      async placesInBbox(_query, candidateLimit) {
        expect(candidateLimit).toBe(480);
        return rows;
      }
    };
    const service = createMapPlacesService(store);

    const result = await service({
      swLat: 52.48,
      swLng: 13.33,
      neLat: 52.56,
      neLng: 13.47,
      zoom: 12
    });

    expect(result.places).toHaveLength(14);
    expect(result.places.slice(0, 12)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          displayKind: "featured"
        })
      ])
    );
    expect(result.places.slice(0, 12).every((place) => place.displayKind === "featured")).toBe(true);
    expect(result.places.slice(12).every((place) => place.displayKind === "dot")).toBe(true);
    expect(result.places.map((item) => item.displayPriority)).toEqual(
      Array.from({ length: 14 }, (_, index) => index + 1)
    );
    expect(result.meta).toEqual({
      returnedCount: 14,
      limit: 120,
      requestedLimit: null,
      candidateLimit: 480,
      capped: false,
      queryBounds: {
        swLat: 52.48,
        swLng: 13.33,
        neLat: 52.56,
        neLng: 13.47
      }
    });
  });

  it("uses the query limit as a lower total cap", async () => {
    const rows = Array.from({ length: 20 }, (_, index) =>
      place({
        id: index + 1,
        source_id: `limited-${index + 1}`
      })
    );
    const store: MapStoreContract = {
      async placesInBbox(_query, candidateLimit) {
        expect(candidateLimit).toBe(40);
        return rows;
      }
    };
    const service = createMapPlacesService(store);

    const result = await service({
      swLat: 52.48,
      swLng: 13.33,
      neLat: 52.56,
      neLng: 13.47,
      limit: 10,
      zoom: 14
    });

    expect(result.places).toHaveLength(10);
    expect(result.places.every((place) => place.displayKind === "featured")).toBe(true);
    expect(result.meta).toEqual({
      returnedCount: 10,
      limit: 10,
      requestedLimit: 10,
      candidateLimit: 40,
      capped: true,
      queryBounds: {
        swLat: 52.48,
        swLng: 13.33,
        neLat: 52.56,
        neLng: 13.47
      }
    });
  });
});
