import { mapPlaceRowToPin } from "../common/map.mappers.js";
import {
  getCandidateLimit,
  getDisplayLimits,
  getEffectiveDisplayLimits,
  rankMapPlaces,
  type MapRankingContext
} from "../common/map.ranking.js";
import type {
  MapPlacePin,
  MapPlacesResult,
  MapPlacesService,
  MapStoreContract
} from "../common/map.types.js";
import { MapStore } from "../stores/map.store.js";
import type { SavedPlacesService } from "../../saved-places/index.js";

export type { MapPlacesService } from "../common/map.types.js";

/**
 * Build the map places provider: fetch candidate rows from the store, rank them
 * by zoom-based density, and map to lightweight pins. Saved state is added
 * separately by `enrichSavedState` so this stays free of cross-module concerns.
 */
export function createMapPlacesService(
  store: MapStoreContract = new MapStore()
): MapPlacesService {
  return async (query) => {
    const displayLimits = getEffectiveDisplayLimits(
      query.limit,
      getDisplayLimits(query, query.zoom)
    );
    const candidateLimit = getCandidateLimit(displayLimits.totalLimit);

    const rows = await store.placesInBbox(query, candidateLimit);
    const context: MapRankingContext = { zoom: query.zoom };
    const ranked = rankMapPlaces(rows, context, displayLimits.totalLimit);

    return {
      places: ranked.map((place, index) => ({
        ...mapPlaceRowToPin(place),
        displayKind: index < displayLimits.featuredLimit ? "featured" : "dot",
        displayPriority: index + 1
      }))
    };
  };
}

export const getMapPlaces = createMapPlacesService();

/**
 * Enrich map pins with the authenticated user's saved state. Public requests
 * (no user) or empty results return pins marked unsaved.
 */
export async function enrichSavedState(
  result: MapPlacesResult,
  userId: string | undefined,
  savedPlacesService: SavedPlacesService
): Promise<MapPlacesResult> {
  if (!userId || result.places.length === 0) {
    return {
      places: result.places.map(markPlaceAsUnsaved)
    };
  }

  const savedPlaceStates = await savedPlacesService.getSavedPlaceStates(
    userId,
    result.places.map((place) => place.id)
  );

  return {
    places: result.places.map((place) => ({
      ...place,
      isSaved: savedPlaceStates.get(place.id)?.isSaved ?? false,
      savedCollectionIds: savedPlaceStates.get(place.id)?.collectionIds ?? []
    }))
  };
}

function markPlaceAsUnsaved(place: MapPlacePin): MapPlacePin {
  return {
    ...place,
    isSaved: false,
    savedCollectionIds: []
  };
}
