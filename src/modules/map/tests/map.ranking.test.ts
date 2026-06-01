import { describe, expect, it } from "vitest";
import {
  deriveZoomFromBbox,
  getCandidateLimit,
  getDisplayLimits,
  getEffectiveDisplayLimits,
  getMapDisplayLimits,
  rankMapPlaces,
  scoreMapPlace,
  type MapRankingContext,
  type ScorablePlace
} from "../common/map.ranking.js";
import {
  getMapGridSize,
  rankSpatiallyBalancedMapPlaces
} from "../common/map.spatial-ranking.js";

const context: MapRankingContext = {};

function place(overrides: Partial<ScorablePlace>): ScorablePlace {
  return {
    source: "osm",
    source_id: "x",
    latitude: 0.5,
    longitude: 0.5,
    rating: null,
    reviews_count: null,
    ...overrides
  };
}

describe("getMapDisplayLimits", () => {
  it("returns expected limits per zoom bucket", () => {
    expect(getMapDisplayLimits(5)).toEqual({
      featuredLimit: 8,
      totalLimit: 80
    });
    expect(getMapDisplayLimits(10)).toEqual({
      featuredLimit: 8,
      totalLimit: 80
    });
    expect(getMapDisplayLimits(11)).toEqual({
      featuredLimit: 12,
      totalLimit: 120
    });
    expect(getMapDisplayLimits(12)).toEqual({
      featuredLimit: 12,
      totalLimit: 120
    });
    expect(getMapDisplayLimits(13)).toEqual({
      featuredLimit: 20,
      totalLimit: 180
    });
    expect(getMapDisplayLimits(14)).toEqual({
      featuredLimit: 20,
      totalLimit: 180
    });
    expect(getMapDisplayLimits(16)).toEqual({
      featuredLimit: 30,
      totalLimit: 220
    });
    expect(getMapDisplayLimits(17)).toEqual({
      featuredLimit: 40,
      totalLimit: 250
    });
    expect(getMapDisplayLimits(20)).toEqual({
      featuredLimit: 40,
      totalLimit: 250
    });
  });
});

describe("getCandidateLimit", () => {
  it("overfetches 4x and caps at 1000", () => {
    expect(getCandidateLimit(80)).toBe(320);
    expect(getCandidateLimit(250)).toBe(1000);
    expect(getCandidateLimit(400)).toBe(1000);
  });
});

describe("getEffectiveDisplayLimits", () => {
  const displayLimits = {
    featuredLimit: 20,
    totalLimit: 180
  };

  it("uses display limits when the user limit is absent", () => {
    expect(getEffectiveDisplayLimits(undefined, displayLimits)).toEqual(
      displayLimits
    );
  });

  it("never exceeds the total limit", () => {
    expect(getEffectiveDisplayLimits(200, displayLimits)).toEqual(displayLimits);
  });

  it("honors a smaller user limit", () => {
    expect(getEffectiveDisplayLimits(10, displayLimits)).toEqual({
      featuredLimit: 10,
      totalLimit: 10
    });
  });
});

describe("deriveZoomFromBbox", () => {
  it("returns a higher zoom for a smaller viewport", () => {
    const wide = deriveZoomFromBbox({ swLat: 0, swLng: 0, neLat: 1, neLng: 10 });
    const narrow = deriveZoomFromBbox({
      swLat: 0,
      swLng: 0,
      neLat: 0.01,
      neLng: 0.01
    });

    expect(narrow).toBeGreaterThan(wide);
  });

  it("stays within the 1..22 range", () => {
    const worldWide = deriveZoomFromBbox({
      swLat: 0,
      swLng: 0,
      neLat: 90,
      neLng: 360
    });
    const tiny = deriveZoomFromBbox({ swLat: 0, swLng: 0, neLat: 0, neLng: 0 });

    expect(worldWide).toBeGreaterThanOrEqual(1);
    expect(tiny).toBeLessThanOrEqual(22);
  });
});

describe("getDisplayLimits", () => {
  it("uses the zoom bucket when zoom is provided", () => {
    const bbox = { swLat: 0, swLng: 0, neLat: 1, neLng: 1 };

    expect(getDisplayLimits(bbox, 13)).toEqual({
      featuredLimit: 20,
      totalLimit: 180
    });
  });

  it("falls back to bbox-derived display limits when zoom is absent", () => {
    const bbox = { swLat: 0, swLng: 0, neLat: 0.01, neLng: 0.01 };

    expect(getDisplayLimits(bbox)).toEqual({
      featuredLimit: 30,
      totalLimit: 220
    });
  });
});

describe("getMapGridSize", () => {
  it("returns expected grid sizes per zoom bucket", () => {
    expect(getMapGridSize(10)).toBe(4);
    expect(getMapGridSize(12)).toBe(4);
    expect(getMapGridSize(14)).toBe(5);
    expect(getMapGridSize(16)).toBe(5);
    expect(getMapGridSize(17)).toBe(6);
  });
});

describe("scoreMapPlace", () => {
  it("prefers a higher rating", () => {
    const high = scoreMapPlace(place({ source_id: "a", rating: 5 }), context);
    const low = scoreMapPlace(place({ source_id: "a", rating: 1 }), context);

    expect(high).toBeGreaterThan(low);
  });

  it("prefers the imported map visibility score when available", () => {
    const visible = scoreMapPlace(
      place({ source_id: "a", map_visibility_score: 90, rating: 1 }),
      context
    );
    const hidden = scoreMapPlace(
      place({ source_id: "a", map_visibility_score: 10, rating: 5 }),
      context
    );

    expect(visible).toBeGreaterThan(hidden);
  });

  it("handles null rating and reviews", () => {
    expect(() => scoreMapPlace(place({}), context)).not.toThrow();
  });

  it("is stable for identical input", () => {
    const first = scoreMapPlace(place({ source_id: "abc", rating: 4 }), context);
    const second = scoreMapPlace(
      place({ source_id: "abc", rating: 4 }),
      context
    );

    expect(first).toBe(second);
  });
});

describe("rankMapPlaces", () => {
  it("returns at most the limit", () => {
    const rows = [
      place({ source_id: "1" }),
      place({ source_id: "2" }),
      place({ source_id: "3" })
    ];

    expect(rankMapPlaces(rows, context, 2)).toHaveLength(2);
  });

  it("orders by score descending", () => {
    const strong = place({
      source_id: "strong",
      source: "google",
      rating: 5,
      reviews_count: 100
    });
    const weak = place({ source_id: "weak", rating: null });

    const ranked = rankMapPlaces([weak, strong], context, 2);

    expect(ranked[0]).toBe(strong);
  });

  it("is stable across repeated calls", () => {
    const rows = [
      place({ source_id: "a", rating: 4 }),
      place({ source_id: "b", rating: 4 }),
      place({ source_id: "c", rating: 4 })
    ];

    expect(rankMapPlaces(rows, context, 3)).toEqual(
      rankMapPlaces(rows, context, 3)
    );
  });
});

describe("rankSpatiallyBalancedMapPlaces", () => {
  const bbox = {
    swLat: 0,
    swLng: 0,
    neLat: 10,
    neLng: 10
  };

  it("does not let one dense cell starve other populated cells", () => {
    const denseCluster = Array.from({ length: 20 }, (_, index) =>
      place({
        source_id: `dense-${index}`,
        latitude: 1,
        longitude: 1,
        map_visibility_score: 100,
        rating: 5
      })
    );
    const sparseSouthEast = place({
      source_id: "south-east",
      latitude: 9,
      longitude: 9,
      map_visibility_score: 10,
      rating: 3
    });
    const sparseNorthEast = place({
      source_id: "north-east",
      latitude: 1,
      longitude: 9,
      map_visibility_score: 10,
      rating: 3
    });

    const ranked = rankSpatiallyBalancedMapPlaces(
      [...denseCluster, sparseSouthEast, sparseNorthEast],
      { zoom: 14 },
      bbox,
      3
    );

    expect(ranked).toContain(sparseSouthEast);
    expect(ranked).toContain(sparseNorthEast);
  });

  it("returns at most the limit and stays deterministic", () => {
    const rows = Array.from({ length: 20 }, (_, index) =>
      place({
        source_id: `place-${index}`,
        latitude: index % 10,
        longitude: index % 5,
        rating: 4
      })
    );

    const first = rankSpatiallyBalancedMapPlaces(rows, { zoom: 14 }, bbox, 7);
    const second = rankSpatiallyBalancedMapPlaces(rows, { zoom: 14 }, bbox, 7);

    expect(first).toHaveLength(7);
    expect(first).toEqual(second);
  });
});
