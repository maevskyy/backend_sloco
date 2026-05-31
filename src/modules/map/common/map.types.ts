import type { z } from "zod";
import type {
  MapPlacesQuery,
  mapPlaceSchema,
  mapPlacesResponseSchema
} from "./map.schemas.js";

export type { MapPlacesQuery };

// Raw row returned by the `places_in_bbox` RPC (DB shape, hand-written).
export type PlaceRow = {
  id: number;
  source: string;
  source_id: string;
  name: string;
  country: string;
  city: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_level: number | null;
  reviews_count: number | null;
  attributes: Record<string, unknown> | null;
};

// HTTP DTO types inferred from the zod schemas.
export type MapPlacePin = z.infer<typeof mapPlaceSchema>;
export type MapPlacesResult = z.infer<typeof mapPlacesResponseSchema>;

export type MapStoreContract = {
  placesInBbox(query: MapPlacesQuery, candidateLimit: number): Promise<PlaceRow[]>;
};

export type MapPlacesService = (
  query: MapPlacesQuery
) => Promise<MapPlacesResult>;
