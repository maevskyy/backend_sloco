import type { MapPlacePin, PlaceRow } from "./map.types.js";

export function mapPlaceRowToPin(row: PlaceRow): MapPlacePin {
  return {
    id: row.id,
    name: row.name,
    category: row.category,
    primaryType: row.primary_type,
    latitude: row.latitude,
    longitude: row.longitude,
    rating: row.rating,
    priceLevel: row.price_level,
    mapVisibilityScore: row.map_visibility_score ?? 0,
    primaryPhoto: mapPrimaryPhoto(row),
    isSaved: false,
    displayKind: "dot",
    displayPriority: 1
  };
}

function mapPrimaryPhoto(row: PlaceRow): MapPlacePin["primaryPhoto"] {
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
