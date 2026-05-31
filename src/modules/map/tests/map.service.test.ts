import { describe, expect, it } from "vitest";
import { createMapPlacesService } from "../services/map.service.js";
import type { MapStoreContract, PlaceRow } from "../common/map.types.js";

function place(overrides: Partial<PlaceRow>): PlaceRow {
  return {
    id: 1,
    source: "tripadvisor",
    source_id: "source-1",
    name: "Place",
    country: "Germany",
    city: "Berlin",
    category: "cafe",
    primary_type: "cafe",
    types: ["cafe"],
    latitude: 52.5,
    longitude: 13.4,
    formatted_address: null,
    short_formatted_address: null,
    business_status: null,
    google_maps_uri: null,
    phone: null,
    international_phone: null,
    website_url: null,
    rating: 4,
    price_level: null,
    reviews_count: 10,
    google_rating: 4,
    google_user_rating_count: 10,
    apify_review_count: null,
    apify_rating_avg: null,
    rating_count_for_score: null,
    bayesian_rating: null,
    rating_score_0_100: null,
    popularity_score_0_100: null,
    rating_confidence_0_100: null,
    price_min_ron: null,
    price_max_ron: null,
    map_visibility_score: null,
    map_visibility_rank: null,
    map_min_zoom_global: null,
    ai_card_summary: null,
    ai_place_type_summary: null,
    ai_vibe: null,
    ai_what_to_expect: null,
    ai_food_and_drinks: null,
    ai_price: null,
    ai_service: null,
    ai_the_move: null,
    ai_watch_out: null,
    ai_tags: [],
    ai_tags_json: [],
    ai_confidence: null,
    axis_quiet_lively: null,
    axis_work_social: null,
    axis_day_night: null,
    axis_casual_premium: null,
    axis_drinks_food: null,
    axis_local_tourist: null,
    axis_cheap_expensive: null,
    axis_traditional_experimental: null,
    review_photo_count: 0,
    vibe_photo_count: 0,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    total_photo_count: 0,
    opening_hours: null,
    serves: [],
    features: {},
    google_details: {},
    apify_details: {},
    ai_details: {},
    photo_details: {},
    attributes: null,
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
  });
});
