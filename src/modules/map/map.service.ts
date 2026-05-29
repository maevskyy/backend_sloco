import { getSupabaseClient } from "../../lib/supabase.js";
import type { MapPlacesQuery } from "./map.schemas.js";

type RawTripAdvisorRestaurantRow = {
  id: number;
  tripadvisor_id: string;
  name: string;
  city: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  price_range: string | null;
  number_of_reviews: number | null;
  raw_cuisine_style: string | null;
};

export type MapPlacePin = {
  id: number;
  source: "tripadvisor";
  sourceId: string;
  name: string;
  city: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  priceRange: string | null;
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
  const { data, error } = await getSupabaseClient()
    .from("raw_tripadvisor_restaurants")
    .select(
      [
        "id",
        "tripadvisor_id",
        "name",
        "city",
        "latitude",
        "longitude",
        "rating",
        "price_range",
        "number_of_reviews",
        "raw_cuisine_style"
      ].join(",")
    )
    .eq("city", query.city)
    .gte("latitude", query.swLat)
    .lte("latitude", query.neLat)
    .gte("longitude", query.swLng)
    .lte("longitude", query.neLng)
    .order("rating", {
      ascending: false,
      nullsFirst: false
    })
    .limit(query.limit);

  if (error) {
    throw error;
  }

  const rows = (data ?? []) as unknown as RawTripAdvisorRestaurantRow[];

  return {
    places: rows.map(mapRawRestaurantToPin)
  };
};

function mapRawRestaurantToPin(row: RawTripAdvisorRestaurantRow): MapPlacePin {
  return {
    id: row.id,
    source: "tripadvisor",
    sourceId: row.tripadvisor_id,
    name: row.name,
    city: row.city,
    latitude: row.latitude,
    longitude: row.longitude,
    rating: row.rating,
    priceRange: row.price_range,
    numberOfReviews: row.number_of_reviews,
    rawCuisineStyle: row.raw_cuisine_style
  };
}
