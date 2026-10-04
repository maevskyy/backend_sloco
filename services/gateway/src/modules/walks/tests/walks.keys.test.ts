import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { toCamel, toSnake } from "../common/walks.keys.js";

// Recorded walk-planner calls and responses of the vendored service
// (services/walk-planner/golden/expected): real request echoes, sequences and
// request bodies of plans and edit chains.
const GOLDEN_DIR = new URL(
  "../../../../../walk-planner/golden/expected/bucharest-20261002-d68a311e/",
  import.meta.url
);

const goldenFiles = readdirSync(GOLDEN_DIR)
  .filter((name) => name.endsWith(".json") && name !== "index.json")
  .map((name) => ({
    name,
    json: JSON.parse(readFileSync(new URL(name, GOLDEN_DIR), "utf8")) as unknown
  }));

// Every value under these keys travels app → gateway → service.
const ROUND_TRIP_KEYS = new Set(["request", "sequence", "body"]);

function collectRoundTrips(value: unknown, found: unknown[] = []) {
  if (Array.isArray(value)) {
    value.forEach((item) => collectRoundTrips(item, found));
  } else if (value !== null && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (ROUND_TRIP_KEYS.has(key) && item !== null && typeof item === "object") {
        found.push(item);
      }
      collectRoundTrips(item, found);
    }
  }
  return found;
}

function snakeKeysOutside(value: unknown, path: string[] = []): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => snakeKeysOutside(item, path));
  }
  if (value === null || typeof value !== "object") return [];

  return Object.entries(value).flatMap(([key, item]) => {
    if (key === "params" || key === "geometry") return [];
    const own = /_/.test(key) ? [[...path, key].join(".")] : [];
    return [...own, ...snakeKeysOutside(item, [...path, key])];
  });
}

describe("walk key conversion", () => {
  it("has golden data to check against", () => {
    expect(goldenFiles.length).toBeGreaterThanOrEqual(26);
  });

  it("round-trips every recorded request echo, sequence and body: toSnake(toCamel(x)) === x", () => {
    const objects = goldenFiles.flatMap((file) => collectRoundTrips(file.json));

    expect(objects.length).toBeGreaterThan(300);

    for (const object of objects) {
      expect(toSnake(toCamel(object))).toEqual(object);
    }
  });

  it("leaves no snake_case key in recorded responses outside params and geometry", () => {
    const leftovers = goldenFiles.flatMap((file) =>
      snakeKeysOutside(toCamel(file.json)).map((key) => `${file.name}: ${key}`)
    );

    expect(leftovers).toEqual([]);
  });

  it("names Google CIDs sourceId and keeps data keys, params and enum values", () => {
    const camel = toCamel({
      must_visit_place_ids: ["1"],
      favourite_place_ids: ["2"],
      want_to_go_place_ids: ["3"],
      start: { lat: 1, lon: 2, place_id: "4" },
      places: { "10915586233752676659": { place_id: "10915586233752676659", rating_count: 3 } },
      messages: [
        {
          code: "radius_shrunk",
          stop_index: null,
          params: { search_radius_km: 1.39, hours_day: { open_24h: false } }
        }
      ],
      segments: [
        { quality: "estimate", geometry: { type: "LineString", coordinates: [[26.1, 44.4]] }, geometry_polyline6: "x" }
      ],
      stops: [{ kind: "on_the_way", hours: { day: { open_24h: true } } }]
    });

    expect(camel).toEqual({
      mustVisitSourceIds: ["1"],
      favouriteSourceIds: ["2"],
      wantToGoSourceIds: ["3"],
      start: { lat: 1, lon: 2, sourceId: "4" },
      places: { "10915586233752676659": { sourceId: "10915586233752676659", ratingCount: 3 } },
      messages: [
        {
          code: "radius_shrunk",
          stopIndex: null,
          params: { search_radius_km: 1.39, hours_day: { open_24h: false } }
        }
      ],
      segments: [
        { quality: "estimate", geometry: { type: "LineString", coordinates: [[26.1, 44.4]] }, geometryPolyline6: "x" }
      ],
      stops: [{ kind: "on_the_way", hours: { day: { open24h: true } } }]
    });
  });

  it("drops the gateway's placeId on the way back to the service", () => {
    expect(
      toSnake({
        sequence: [{ sourceId: "5", placeId: 812, kind: "slot", dwellMin: 60 }],
        includeClosed: true
      })
    ).toEqual({
      sequence: [{ place_id: "5", kind: "slot", dwell_min: 60 }],
      include_closed: true
    });
  });
});
