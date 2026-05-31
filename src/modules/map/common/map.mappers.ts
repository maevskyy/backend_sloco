import type { MapPlacePin, PlaceRow } from "./map.types.js";

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
      getStringAttribute(row.attributes, "cuisine"),
    isSaved: false,
    savedCollectionIds: []
  };
}

function getStringAttribute(
  attributes: Record<string, unknown> | null,
  key: string
) {
  const value = attributes?.[key];

  return typeof value === "string" && value.trim().length > 0 ? value : null;
}
