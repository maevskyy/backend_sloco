import { describe, expect, it } from "vitest";
import { MAP_PINS_SAFETY_CAP } from "../common/map.ranking.js";
import { createMapPlacesService } from "../services/map.service.js";
import type {
  MapPlacesQuery,
  MapStoreContract,
  PlaceRow
} from "../common/map.types.js";

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
    map_visibility_score: 80,
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
  it("uses zoom thresholds and returns all rows from the thresholded store result", async () => {
    const rows = Array.from({ length: 20 }, (_, index) =>
      place({
        id: index + 1,
        source_id: `source-${index + 1}`,
        map_visibility_score: index === 0 ? 93 : 80,
        latitude: 52.48 + index * 0.001,
        longitude: 13.33 + index * 0.001
      })
    );
    const store: MapStoreContract = {
      async placesInBbox(_query, minScore, resultLimit) {
        expect(minScore).toBe(76);
        expect(resultLimit).toBe(MAP_PINS_SAFETY_CAP);
        return rows;
      }
    };
    const service = createMapPlacesService(store);

    const result = await service({
      swLat: 52.48,
      swLng: 13.33,
      neLat: 52.56,
      neLng: 13.47,
      zoom: 14
    });

    expect(result.places).toHaveLength(20);
    expect(result.places[0]).not.toHaveProperty("displayKind");
    expect(result.places.map((item) => item.displayPriority)).toEqual(
      Array.from({ length: 20 }, (_, index) => index + 1)
    );
    expect(result.meta).toEqual({
      returnedCount: 20,
      limit: MAP_PINS_SAFETY_CAP,
      requestedLimit: null,
      candidateLimit: MAP_PINS_SAFETY_CAP,
      capped: false,
      effectiveZoom: 14,
      minScore: 76,
      safetyCap: MAP_PINS_SAFETY_CAP,
      capHit: false,
      queryBounds: {
        swLat: 52.48,
        swLng: 13.33,
        neLat: 52.56,
        neLng: 13.47
      }
    });
  });

  it("uses query limit as a safety cap", async () => {
    const rows = Array.from({ length: 10 }, (_, index) =>
      place({
        id: index + 1,
        source_id: `limited-${index + 1}`,
        map_visibility_score: 90
      })
    );
    const store: MapStoreContract = {
      async placesInBbox(_query, minScore, resultLimit) {
        expect(minScore).toBe(76);
        expect(resultLimit).toBe(10);
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
    expect(result.meta).toMatchObject({
      returnedCount: 10,
      limit: 10,
      requestedLimit: 10,
      candidateLimit: 10,
      capped: true,
      capHit: true
    });
  });

  it("keeps the same place stable across overlapping bboxes at the same zoom", async () => {
    const stablePlace = place({
      id: 42,
      source_id: "stable",
      map_visibility_score: 80
    });
    const firstRows = [
      stablePlace,
      ...Array.from({ length: 40 }, (_, index) =>
        place({
          id: index + 100,
          source_id: `first-${index}`,
          map_visibility_score: 77
        })
      )
    ];
    const secondRows = [
      stablePlace,
      ...Array.from({ length: 200 }, (_, index) =>
        place({
          id: index + 200,
          source_id: `second-${index}`,
          map_visibility_score: 77
        })
      )
    ];
    const rowsByCall = [firstRows, secondRows];
    const store: MapStoreContract = {
      async placesInBbox(_query, minScore) {
        expect(minScore).toBe(76);
        return rowsByCall.shift() ?? [];
      }
    };
    const service = createMapPlacesService(store);
    const query: MapPlacesQuery = {
      swLat: 52.48,
      swLng: 13.33,
      neLat: 52.56,
      neLng: 13.47,
      zoom: 14
    };

    const first = await service(query);
    const second = await service({
      ...query,
      swLng: 13.34,
      neLng: 13.48
    });

    expect(first.places.some((item) => item.id === stablePlace.id)).toBe(true);
    expect(second.places.some((item) => item.id === stablePlace.id)).toBe(true);
  });
});
