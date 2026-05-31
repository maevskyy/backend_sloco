import { describe, expect, it } from "vitest";
import { mapPlaceRowToPin } from "../common/map.mappers.js";
import type { MapPlacePin, PlaceRow } from "../common/map.types.js";

describe("map place row mapper", () => {
  it("maps TripAdvisor rows to map pins", () => {
    const row: PlaceRow = {
      id: 1,
      source: "tripadvisor",
      source_id: "d5529357",
      name: "Pane e Vino",
      country: "Germany",
      city: "Berlin",
      latitude: 52.552578,
      longitude: 13.352883,
      rating: 4,
      price_level: 2,
      reviews_count: 17,
      attributes: {
        raw_cuisine_style: "Italian, Pizza, Mediterranean"
      }
    };

    expect(mapPlaceRowToPin(row)).toEqual<MapPlacePin>({
      id: 1,
      source: "tripadvisor",
      sourceId: "d5529357",
      name: "Pane e Vino",
      country: "Germany",
      city: "Berlin",
      latitude: 52.552578,
      longitude: 13.352883,
      rating: 4,
      priceLevel: 2,
      numberOfReviews: 17,
      rawCuisineStyle: "Italian, Pizza, Mediterranean",
      isSaved: false,
      savedCollectionIds: [],
      displayKind: "dot",
      displayPriority: 1
    });
  });

  it("maps OSM rows with missing review signals as null", () => {
    const row: PlaceRow = {
      id: 2,
      source: "osm",
      source_id: "osm:node/4712948976",
      name: "Coffee Shop",
      country: "RO",
      city: "Bucharest",
      latitude: 44.43,
      longitude: 26.1,
      rating: null,
      price_level: null,
      reviews_count: null,
      attributes: {
        cuisine: "coffee_shop"
      }
    };

    expect(mapPlaceRowToPin(row)).toEqual<MapPlacePin>({
      id: 2,
      source: "osm",
      sourceId: "osm:node/4712948976",
      name: "Coffee Shop",
      country: "RO",
      city: "Bucharest",
      latitude: 44.43,
      longitude: 26.1,
      rating: null,
      priceLevel: null,
      numberOfReviews: null,
      rawCuisineStyle: "coffee_shop",
      isSaved: false,
      savedCollectionIds: [],
      displayKind: "dot",
      displayPriority: 1
    });
  });
});
