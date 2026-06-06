import { describe, expect, it } from "vitest";
import { mapSearchRowToResult } from "../common/search.mappers.js";
import type { SearchPlaceResult, SearchPlaceRow } from "../common/search.types.js";

function searchRow(overrides: Partial<SearchPlaceRow> = {}): SearchPlaceRow {
  return {
    id: 1,
    name: "Seneca Anticafe",
    category: "cafe",
    primary_type: "cafe",
    city: "Bucharest",
    country: "RO",
    formatted_address: "Bucharest, Romania",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    price_level: 2,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    distance_m: 830.5,
    match_reason: "name",
    ...overrides
  };
}

describe("search row mapper", () => {
  it("maps a search row to a slim search result", () => {
    expect(
      mapSearchRowToResult(
        searchRow({
          primary_photo_path: "google/ChIJ123/vibe/photo.webp",
          primary_photo_url: "https://example.com/photo.webp",
          primary_photo_width: 1200,
          primary_photo_height: 900,
          primary_photo_source: "vibe"
        })
      )
    ).toEqual<SearchPlaceResult>({
      id: 1,
      name: "Seneca Anticafe",
      category: "cafe",
      primaryType: "cafe",
      city: "Bucharest",
      country: "RO",
      formattedAddress: "Bucharest, Romania",
      latitude: 44.43,
      longitude: 26.1,
      rating: 4.8,
      priceLevel: 2,
      primaryPhoto: {
        path: "google/ChIJ123/vibe/photo.webp",
        url: "https://example.com/photo.webp",
        width: 1200,
        height: 900,
        source: "vibe"
      },
      distanceMeters: 830.5,
      matchReason: "name",
      isSaved: false
    });
  });

  it("uses null for missing optional fields", () => {
    expect(
      mapSearchRowToResult(
        searchRow({
          formatted_address: null,
          rating: null,
          price_level: null,
          primary_photo_path: null,
          distance_m: null,
          match_reason: "tag"
        })
      )
    ).toMatchObject({
      formattedAddress: null,
      rating: null,
      priceLevel: null,
      primaryPhoto: null,
      distanceMeters: null,
      matchReason: "tag",
      isSaved: false
    });
  });
});
