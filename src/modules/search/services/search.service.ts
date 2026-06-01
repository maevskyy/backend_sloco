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
    const rows = await store.searchPlaces(query);

    return {
      query: query.q,
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
