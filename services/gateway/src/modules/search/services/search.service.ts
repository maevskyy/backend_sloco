import { bucketsToKeywords } from "../../places/index.js";
import { mapSearchRowToResult } from "../common/search.mappers.js";
import type {
  SearchPlaceResult,
  SearchPlacesResult,
  SearchPlacesService,
  SearchStoreContract
} from "../common/search.types.js";
import { SearchStore } from "../stores/search.store.js";
import type { SavedPlacesService } from "../../saved-places/index.js";

export type { SearchPlacesService } from "../common/search.types.js";

export function createSearchPlacesService(
  store: SearchStoreContract = new SearchStore()
): SearchPlacesService {
  return async (query) => {
    const rows = await store.searchPlaces({
      q: query.q ?? null,
      lat: query.lat ?? null,
      lng: query.lng ?? null,
      city: query.city ?? null,
      country: query.country ?? null,
      limit: query.limit,
      categoryKeywords: query.category ? bucketsToKeywords(query.category) : null,
      radiusMeters: query.radiusMeters ?? null
    });

    return {
      // Browse mode (category-only) has no q; the response field stays a
      // string for the existing client decoder.
      query: query.q ?? "",
      places: rows.map(mapSearchRowToResult)
    };
  };
}

export const getSearchPlaces = createSearchPlacesService();

export async function enrichSearchSavedState(
  result: SearchPlacesResult,
  userId: string | undefined,
  savedPlacesService: SavedPlacesService
): Promise<SearchPlacesResult> {
  if (!userId || result.places.length === 0) {
    return {
      query: result.query,
      places: result.places.map(markPlaceAsUnsaved)
    };
  }

  const savedPlaceIds = await savedPlacesService.getSavedPlaceIds(
    userId,
    result.places.map((place) => place.id)
  );

  return {
    query: result.query,
    places: result.places.map((place) => ({
      ...place,
      isSaved: savedPlaceIds.has(place.id)
    }))
  };
}

function markPlaceAsUnsaved(place: SearchPlaceResult): SearchPlaceResult {
  return {
    ...place,
    isSaved: false
  };
}
