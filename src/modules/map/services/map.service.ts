import { mapPlaceRowToPin } from "../common/map.mappers.js";
import {
  getCandidateLimit,
  getDisplayLimits,
  getEffectiveDisplayLimits,
  type MapRankingContext
} from "../common/map.ranking.js";
import { rankSpatiallyBalancedMapPlaces } from "../common/map.spatial-ranking.js";
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
    const ranked = rankSpatiallyBalancedMapPlaces(
      rows,
      context,
      query,
      displayLimits.totalLimit
    );

    return {
      places: ranked.map((place, index) => ({
        ...mapPlaceRowToPin(place),
        displayKind: index < displayLimits.featuredLimit ? "featured" : "dot",
        displayPriority: index + 1
      })),
      meta: {
        returnedCount: ranked.length,
        limit: displayLimits.totalLimit,
        requestedLimit: query.limit ?? null,
        candidateLimit,
        capped:
          rows.length >= candidateLimit ||
          ranked.length >= displayLimits.totalLimit,
        queryBounds: {
          swLat: query.swLat,
          swLng: query.swLng,
          neLat: query.neLat,
          neLng: query.neLng
        }
      }
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
      ...result,
      places: result.places.map(markPlaceAsUnsaved)
    };
  }

  const savedPlaceIds = await savedPlacesService.getSavedPlaceIds(
    userId,
    result.places.map((place) => place.id)
  );

  return {
    ...result,
    places: result.places.map((place) => ({
      ...place,
      isSaved: savedPlaceIds.has(place.id)
    }))
  };
}

function markPlaceAsUnsaved(place: MapPlacePin): MapPlacePin {
  return {
    ...place,
    isSaved: false
  };
}
