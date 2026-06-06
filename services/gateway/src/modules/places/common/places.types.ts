import type { z } from "zod";
import type {
  placeDetailsResponseSchema,
  placeDetailsSchema
} from "./places.schemas.js";

export type JsonObject = Record<string, unknown>;

export type PlaceDetailRow = {
  id: number;
  source: string;
  source_id: string;
  name: string;
  country: string;
  city: string;
  category: string;
  primary_type: string | null;
  types: string[] | null;
  latitude: number;
  longitude: number;
  formatted_address: string | null;
  short_formatted_address: string | null;
  business_status: string | null;
  google_maps_uri: string | null;
  phone: string | null;
  international_phone: string | null;
  website_url: string | null;
  rating: number | null;
  price_level: number | null;
  reviews_count: number | null;
  google_rating: number | null;
  google_user_rating_count: number | null;
  apify_review_count: number | null;
  apify_rating_avg: number | null;
  rating_count_for_score: number | null;
  bayesian_rating: number | null;
  rating_score_0_100: number | null;
  popularity_score_0_100: number | null;
  rating_confidence_0_100: number | null;
  price_min_ron: number | null;
  price_max_ron: number | null;
  map_visibility_score: number | null;
  map_visibility_rank: number | null;
  map_min_zoom_global: number | null;
  ai_card_summary: string | null;
  ai_place_type_summary: string | null;
  ai_vibe: string | null;
  ai_what_to_expect: string | null;
  ai_food_and_drinks: string | null;
  ai_price: string | null;
  ai_service: string | null;
  ai_the_move: string | null;
  ai_watch_out: string | null;
  ai_tags: string[] | null;
  ai_tags_json: unknown;
  ai_confidence: number | null;
  axis_quiet_lively: number | null;
  axis_work_social: number | null;
  axis_day_night: number | null;
  axis_casual_premium: number | null;
  axis_drinks_food: number | null;
  axis_local_tourist: number | null;
  axis_cheap_expensive: number | null;
  axis_traditional_experimental: number | null;
  review_photo_count: number | null;
  vibe_photo_count: number | null;
  primary_photo_path: string | null;
  primary_photo_url: string | null;
  primary_photo_width: number | null;
  primary_photo_height: number | null;
  primary_photo_source: string | null;
  total_photo_count: number | null;
  opening_hours: unknown;
  serves: unknown;
  features: unknown;
  google_details: JsonObject | null;
  apify_details: JsonObject | null;
  ai_details: JsonObject | null;
  photo_details: JsonObject | null;
  attributes: JsonObject | null;
};

export type PlaceDetails = z.infer<typeof placeDetailsSchema>;
export type PlaceDetailsResult = z.infer<typeof placeDetailsResponseSchema>;

export type PlacesStoreContract = {
  placeDetailsById(placeId: number): Promise<PlaceDetailRow | null>;
};

export type PlaceDetailsService = (
  placeId: number
) => Promise<PlaceDetailsResult | null>;
