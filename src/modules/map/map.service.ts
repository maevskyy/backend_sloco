import { getSupabaseClient } from "../../lib/supabase.js";
import {
  getCandidateLimit,
  getDensityLimit,
  getEffectiveLimit,
  rankMapPlaces,
  type MapRankingContext
} from "./map.ranking.js";
import type { MapPlacesQuery } from "./map.schemas.js";

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

export type MapPlacePin = {
  id: number;
  source: string;
  sourceId: string;
  name: string;
  country: string;
  city: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  priceLevel: number | null;
  numberOfReviews: number | null;
  rawCuisineStyle: string | null;
};

export type MapPlacesResult = {
  places: MapPlacePin[];
};

export type MapPlacesService = (
  query: MapPlacesQuery
) => Promise<MapPlacesResult>;

export const getMapPlaces: MapPlacesService = async (query) => {
  const densityLimit = getDensityLimit(query, query.zoom);
  const effectiveLimit = getEffectiveLimit(query.limit, densityLimit);
  const candidateLimit = getCandidateLimit(effectiveLimit);

  const { data, error } = await getSupabaseClient().rpc("places_in_bbox", {
    sw_lat: query.swLat,
    sw_lng: query.swLng,
    ne_lat: query.neLat,
    ne_lng: query.neLng,
    result_limit: candidateLimit
  });

  if (error) {
    throw error;
  }

  const rows = (data ?? []) as unknown as PlaceRow[];
  const context: MapRankingContext = { zoom: query.zoom };
  const ranked = rankMapPlaces(rows, context, effectiveLimit);

  return {
    places: ranked.map(mapPlaceRowToPin)
  };
};

export function mapPlaceRowToPin(row: PlaceRow): MapPlacePin {
  return {
    id: row.id,
    source: row.source,
    sourceId: row.source_id,
    name: row.name,
    country: row.country,
    city: row.city,
    latitude: row.latitude,
    longitude: row.longitude,
    rating: row.rating,
    priceLevel: row.price_level,
    numberOfReviews: row.reviews_count,
    rawCuisineStyle:
      getStringAttribute(row.attributes, "raw_cuisine_style") ??
      getStringAttribute(row.attributes, "cuisine")
  };
}

function getStringAttribute(
  attributes: Record<string, unknown> | null,
  key: string
) {
  const value = attributes?.[key];

  return typeof value === "string" && value.trim().length > 0 ? value : null;
}
