import type { z } from "zod";
import type {
  MapPlacesQuery,
  mapPlacePinSchema,
  mapPlacesResponseSchema
} from "./map.schemas.js";

export type { MapPlacesQuery };

export type JsonObject = Record<string, unknown>;

// Raw row returned by the `places_in_bbox` RPC (DB shape, hand-written).
export type PlaceRow = {
  id: number;
  source: string;
  source_id: string;
  name: string;
  category: string;
  primary_type: string | null;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  reviews_count: number | null;
  google_rating: number | null;
  google_user_rating_count: number | null;
  rating_score_0_100: number | null;
  popularity_score_0_100: number | null;
  map_visibility_score: number | null;
  map_visibility_rank: number | null;
  primary_photo_path: string | null;
  primary_photo_url: string | null;
  primary_photo_width: number | null;
  primary_photo_height: number | null;
  primary_photo_source: string | null;
};

// HTTP DTO types inferred from the zod schemas.
export type MapPlacePin = z.infer<typeof mapPlacePinSchema>;
export type MapPlacesResult = z.infer<typeof mapPlacesResponseSchema>;

export type MapStoreContract = {
  placesInBbox(
    query: MapPlacesQuery,
    minScore: number,
    resultLimit: number
  ): Promise<PlaceRow[]>;
};

export type MapPlacesService = (
  query: MapPlacesQuery
) => Promise<MapPlacesResult>;
