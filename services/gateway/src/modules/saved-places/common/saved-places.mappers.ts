import type {
  PlaceRecord,
  SavedCategory,
  SavedCollection,
  SavedCollectionPlaceRow,
  SavedCollectionRow,
  SavedPlaceRow,
  SavedPlaceSummary
} from "./saved-places.types.js";

export function mapCollectionRow(
  row: SavedCollectionRow,
  places: SavedPlaceSummary[]
): SavedCollection {
  return {
    id: row.id,
    name: row.name,
    colorHex: row.color_hex,
    placeCount: places.length,
    placeIds: places.map((place) => place.id),
    previewPlaces: places.slice(0, 3),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    slug: row.slug,
    isDefault: row.is_default,
    sortOrder: row.sort_order
  };
}

export function stripPreviewPlaces(
  collection: SavedCollection
): Omit<SavedCollection, "previewPlaces"> {
  return {
    id: collection.id,
    name: collection.name,
    colorHex: collection.colorHex,
    placeCount: collection.placeCount,
    placeIds: collection.placeIds,
    createdAt: collection.createdAt,
    updatedAt: collection.updatedAt,
    slug: collection.slug,
    isDefault: collection.isDefault,
    sortOrder: collection.sortOrder
  };
}

export function mapSavedPlaceRow(row: SavedPlaceRow): SavedPlaceSummary {
  return mapPlaceSummary(
    normalizePlaceRecord(row.places),
    row.created_at,
    row.last_viewed_at
  );
}

export function mapCollectionPlaceRow(
  row: SavedCollectionPlaceRow
): SavedPlaceSummary {
  return mapPlaceSummary(normalizePlaceRecord(row.places), row.created_at, null);
}

function mapPlaceSummary(
  place: PlaceRecord,
  savedAt: string,
  lastViewedAt: string | null
): SavedPlaceSummary {
  const category = normalizeCategory(place.category, place.attributes);

  return {
    id: place.id,
    source: place.source,
    sourceId: place.source_id,
    name: place.name,
    city: place.city,
    country: place.country,
    latitude: place.latitude,
    longitude: place.longitude,
    category,
    categoryLabel: getCategoryLabel(category),
    rating: place.rating,
    priceLevel: normalizePriceLevel(place.price_level),
    tags: extractTags(place),
    distanceText: null,
    imageUrl: null,
    savedAt,
    lastViewedAt
  };
}

function normalizePlaceRecord(
  place: PlaceRecord | PlaceRecord[] | null
): PlaceRecord {
  if (Array.isArray(place)) {
    const firstPlace = place[0];

    if (firstPlace) {
      return firstPlace;
    }

    throw new Error("Saved place row is missing joined place data");
  }

  if (place) {
    return place;
  }

  throw new Error("Saved place row is missing joined place data");
}

function normalizeCategory(
  category: string,
  attributes: Record<string, unknown> | null
): SavedCategory {
  const searchable = [
    category,
    getStringAttribute(attributes, "raw_cuisine_style"),
    getStringAttribute(attributes, "cuisine"),
    getStringAttribute(attributes, "embedding_text")
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (searchable.match(/coffee|cafe|espresso|brunch/)) return "cafe";
  if (searchable.match(/bar|pub|wine|cocktail|beer/)) return "bar";
  if (searchable.match(/museum|gallery|art|culture|theatre|theater/)) {
    return "culture";
  }
  if (searchable.match(/music|jazz|club|techno|concert/)) return "music";
  if (searchable.match(/park|garden|nature|walk/)) return "nature";
  if (
    searchable.match(
      /restaurant|food|pizza|sushi|asian|italian|german|cuisine|vegan|vegetarian/
    )
  ) {
    return "food";
  }

  return "other";
}

function getCategoryLabel(category: SavedCategory) {
  const labels: Record<SavedCategory, string> = {
    food: "Food",
    cafe: "Cafe",
    bar: "Bar",
    nature: "Nature",
    culture: "Culture",
    music: "Music",
    other: "Other"
  };

  return labels[category];
}

function normalizePriceLevel(priceLevel: number | null): 0 | 1 | 2 | 3 | 4 | null {
  if (priceLevel === null) {
    return null;
  }

  return [0, 1, 2, 3, 4].includes(priceLevel)
    ? (priceLevel as 0 | 1 | 2 | 3 | 4)
    : null;
}

function extractTags(place: PlaceRecord) {
  const rawCuisineStyle =
    getStringAttribute(place.attributes, "raw_cuisine_style") ??
    getStringAttribute(place.attributes, "cuisine");

  if (!rawCuisineStyle) {
    return [getCategoryLabel(normalizeCategory(place.category, place.attributes))];
  }

  return rawCuisineStyle
    .replaceAll("[", "")
    .replaceAll("]", "")
    .replaceAll("'", "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 3);
}

function getStringAttribute(
  attributes: Record<string, unknown> | null,
  key: string
) {
  const value = attributes?.[key];

  return typeof value === "string" && value.trim().length > 0 ? value : null;
}
