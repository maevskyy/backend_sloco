import { describe, expect, it } from "vitest";
import {
  deriveZoomFromBbox,
  getCandidateLimit,
  getDensityLimit,
  getEffectiveLimit,
  getMapDensityLimit,
  rankMapPlaces,
  scoreMapPlace,
  type MapRankingContext,
  type ScorablePlace
} from "./map.ranking.js";

const context: MapRankingContext = {};

function place(overrides: Partial<ScorablePlace>): ScorablePlace {
  return {
    source: "osm",
    source_id: "x",
    rating: null,
    reviews_count: null,
    ...overrides
  };
}

describe("getMapDensityLimit", () => {
  it("returns expected limits per zoom bucket", () => {
    expect(getMapDensityLimit(5)).toBe(8);
    expect(getMapDensityLimit(10)).toBe(8);
    expect(getMapDensityLimit(11)).toBe(15);
    expect(getMapDensityLimit(12)).toBe(15);
    expect(getMapDensityLimit(13)).toBe(25);
    expect(getMapDensityLimit(14)).toBe(25);
    expect(getMapDensityLimit(15)).toBe(40);
    expect(getMapDensityLimit(20)).toBe(40);
  });
});

describe("getCandidateLimit", () => {
  it("overfetches 4x and caps at 400", () => {
    expect(getCandidateLimit(8)).toBe(32);
    expect(getCandidateLimit(25)).toBe(100);
    expect(getCandidateLimit(200)).toBe(400);
  });
});

describe("getEffectiveLimit", () => {
  it("uses density when the user limit is absent", () => {
    expect(getEffectiveLimit(undefined, 25)).toBe(25);
  });

  it("never exceeds density", () => {
    expect(getEffectiveLimit(200, 25)).toBe(25);
  });

  it("honors a smaller user limit", () => {
    expect(getEffectiveLimit(10, 25)).toBe(10);
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

describe("getDensityLimit", () => {
  it("uses the zoom bucket when zoom is provided", () => {
    const bbox = { swLat: 0, swLng: 0, neLat: 1, neLng: 1 };

    expect(getDensityLimit(bbox, 13)).toBe(25);
  });

  it("falls back to bbox-derived density when zoom is absent", () => {
    const bbox = { swLat: 0, swLng: 0, neLat: 0.01, neLng: 0.01 };

    expect(getDensityLimit(bbox)).toBe(40);
  });
});

describe("scoreMapPlace", () => {
  it("prefers a higher rating", () => {
    const high = scoreMapPlace(place({ source_id: "a", rating: 5 }), context);
    const low = scoreMapPlace(place({ source_id: "a", rating: 1 }), context);

    expect(high).toBeGreaterThan(low);
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
      source: "tripadvisor",
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
