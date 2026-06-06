import type { z } from "zod";
import type { AuthenticatedUser } from "../../auth/auth.service.js";
import type {
  feedCacheStatusSchema,
  feedInputSummarySchema,
  feedPlaceCardSchema,
  feedPlacesQuerySchema,
  feedPlacesResponseSchema,
  feedPersonalizationStatusSchema
} from "./feed.schemas.js";

export type FeedPrimaryPhotoFields = {
  primary_photo_path: string | null;
  primary_photo_url: string | null;
  primary_photo_width: number | null;
  primary_photo_height: number | null;
  primary_photo_source: string | null;
};

export type FeedPlaceRow = FeedPrimaryPhotoFields & {
  id: number;
  source: string;
  source_id: string;
  name: string;
  country: string;
  city: string;
  category: string;
  primary_type: string | null;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  reviews_count: number | null;
  map_visibility_score: number | null;
  ai_card_summary: string | null;
  ai_place_type_summary: string | null;
  ai_vibe: string | null;
  ai_the_move: string | null;
  ai_tags: string[] | null;
  formatted_address: string | null;
  distance_m: number | null;
};

export type FeedSavedSignals = {
  favouritesPlaceIds: string[];
  wantToGoPlaceIds: string[];
};

export type FeedRecommendationItem = {
  rank: number;
  place_id: string;
  score: number;
  similarity?: number | null;
};

export type FeedRecommendationInputSummary = {
  favourites_count: number;
  want_to_go_count: number;
  valid_input_count: number;
  invalid_place_ids: string[];
  candidate_count?: number;
};

export type FeedRecommendationResponse = {
  user_id: string | null;
  algorithm_version: string;
  embedding_run_id: string;
  input_summary: FeedRecommendationInputSummary;
  recommendations: FeedRecommendationItem[];
};

export type FeedRecommendationRequest = {
  user_id: string;
  favourites_place_ids: string[];
  want_to_go_place_ids: string[];
  limit: number;
  exclude_input_places: boolean;
  debug: boolean;
};

export type FeedRecommendationClient = {
  personalizedPlaces(
    request: FeedRecommendationRequest
  ): Promise<FeedRecommendationResponse>;
};

export type FeedRecommendationSeed = {
  rank: number;
  sourceId: string;
  score: number;
};

export type FeedPlacesQuery = z.infer<typeof feedPlacesQuerySchema>;
export type FeedPlaceCard = z.infer<typeof feedPlaceCardSchema>;
export type FeedPlacesResult = z.infer<typeof feedPlacesResponseSchema>;
export type FeedInputSummary = z.infer<typeof feedInputSummarySchema>;
export type FeedPersonalizationStatus = z.infer<
  typeof feedPersonalizationStatusSchema
>;
export type FeedCacheStatus = z.infer<typeof feedCacheStatusSchema>;

export type FeedStoreContract = {
  getSavedSignals(userId: string): Promise<FeedSavedSignals>;
  feedPlacesBySourceIds(
    sourceIds: string[],
    query: FeedPlacesQuery,
    limit: number
  ): Promise<FeedPlaceRow[]>;
  fallbackFeedPlaces(
    query: FeedPlacesQuery,
    limit: number
  ): Promise<FeedPlaceRow[]>;
};

export type FeedPlacesServiceInput = {
  query: FeedPlacesQuery;
  user: AuthenticatedUser | null;
};

export type FeedPlacesService = (
  input: FeedPlacesServiceInput
) => Promise<FeedPlacesResult>;
