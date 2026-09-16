import type { z } from "zod";
import type {
  searchPlaceResultSchema,
  searchPlacesQuerySchema,
  searchPlacesResponseSchema
} from "./search.schemas.js";

export type SearchPlaceRow = {
  id: number;
  name: string;
  category: string;
  primary_type: string | null;
  city: string;
  country: string;
  formatted_address: string | null;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  primary_photo_path: string | null;
  primary_photo_url: string | null;
  primary_photo_width: number | null;
  primary_photo_height: number | null;
  primary_photo_source: string | null;
  distance_m: number | null;
  match_reason: "name" | "category" | "type" | "tag";
};

export type SearchPlacesQuery = z.infer<typeof searchPlacesQuerySchema>;
export type SearchPlaceResult = z.infer<typeof searchPlaceResultSchema>;
export type SearchPlacesResult = z.infer<typeof searchPlacesResponseSchema>;

// The wire-ready shape the store sends to the RPC: buckets already flattened
// into keywords (the service owns that mapping).
export type SearchStoreInput = {
  q: string | null;
  lat: number | null;
  lng: number | null;
  city: string | null;
  country: string | null;
  limit: number;
  categoryKeywords: string[] | null;
  radiusMeters: number | null;
};

export type SearchStoreContract = {
  searchPlaces(input: SearchStoreInput): Promise<SearchPlaceRow[]>;
};

export type SearchPlacesService = (
  query: SearchPlacesQuery
) => Promise<SearchPlacesResult>;
