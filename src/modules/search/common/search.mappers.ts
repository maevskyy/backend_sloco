import type { SearchPlaceResult, SearchPlaceRow } from "./search.types.js";

export function mapSearchRowToResult(row: SearchPlaceRow): SearchPlaceResult {
  return {
    id: row.id,
    name: row.name,
    category: row.category,
    primaryType: row.primary_type,
    city: row.city,
    country: row.country,
    formattedAddress: row.formatted_address,
    latitude: row.latitude,
    longitude: row.longitude,
    rating: row.rating,
    priceLevel: row.price_level,
    primaryPhoto: mapPrimaryPhoto(row),
    distanceMeters: row.distance_m,
    matchReason: row.match_reason,
    isSaved: false
  };
}

function mapPrimaryPhoto(row: SearchPlaceRow): SearchPlaceResult["primaryPhoto"] {
  if (!row.primary_photo_path) {
    return null;
  }

  return {
    path: row.primary_photo_path,
    url: row.primary_photo_url,
    width: row.primary_photo_width,
    height: row.primary_photo_height,
    source: row.primary_photo_source
  };
}
