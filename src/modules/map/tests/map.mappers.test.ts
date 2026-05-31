import { describe, expect, it } from "vitest";
import { mapPlaceRowToPin } from "../common/map.mappers.js";
import type { MapPlacePin, PlaceRow } from "../common/map.types.js";

function placeRow(overrides: Partial<PlaceRow> = {}): PlaceRow {
  return {
    id: 1,
    source: "google",
    source_id: "ChIJ123",
    name: "Seneca Anticafe",
    category: "cafe",
    primary_type: "cafe",
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    price_level: 2,
    reviews_count: 1411,
    google_rating: 4.8,
    google_user_rating_count: 1411,
    rating_score_0_100: 92,
    popularity_score_0_100: 80,
    map_visibility_score: 89,
    map_visibility_rank: 1,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    ...overrides
  };
}

describe("map place row mapper", () => {
  it("maps a place row to a lightweight map pin", () => {
    expect(
      mapPlaceRowToPin(
        placeRow({
          primary_photo_path: "google/ChIJ123/vibe/photo.jpg",
          primary_photo_url: "https://example.com/photo.jpg",
          primary_photo_width: 1200,
          primary_photo_height: 900,
          primary_photo_source: "vibe"
        })
      )
    ).toEqual<MapPlacePin>({
      id: 1,
      name: "Seneca Anticafe",
      category: "cafe",
      primaryType: "cafe",
      latitude: 44.43,
      longitude: 26.1,
      rating: 4.8,
      priceLevel: 2,
      mapVisibilityScore: 89,
      primaryPhoto: {
        path: "google/ChIJ123/vibe/photo.jpg",
        url: "https://example.com/photo.jpg",
        width: 1200,
        height: 900,
        source: "vibe"
      },
      isSaved: false,
      displayKind: "dot",
      displayPriority: 1
    });
  });

  it("does not expose detail-only fields in map pins", () => {
    const pin = mapPlaceRowToPin(placeRow());

    expect(pin).not.toHaveProperty("source");
    expect(pin).not.toHaveProperty("sourceId");
    expect(pin).not.toHaveProperty("country");
    expect(pin).not.toHaveProperty("city");
    expect(pin).not.toHaveProperty("formattedAddress");
    expect(pin).not.toHaveProperty("googleDetails");
    expect(pin).not.toHaveProperty("aiDetails");
    expect(pin).not.toHaveProperty("openingHours");
    expect(pin).not.toHaveProperty("savedCollectionIds");
  });

  it("uses null for missing primary photo and defaults visibility score", () => {
    expect(
      mapPlaceRowToPin(
        placeRow({
          map_visibility_score: null,
          primary_photo_path: null
        })
      )
    ).toMatchObject({
      mapVisibilityScore: 0,
      primaryPhoto: null
    });
  });
});
