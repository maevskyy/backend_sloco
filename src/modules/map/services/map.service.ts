import { mapPlaceRowToPin } from "../common/map.mappers.js";
import {
  getEffectiveMapZoom,
  getMapPinsSafetyCap,
  getMapVisibilityThresholds,
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
 * Build the map places provider: fetch rows above the zoom visibility threshold,
 * rank them spatially, and map to lightweight pins. Saved state is added
 * separately by `enrichSavedState` so this stays free of cross-module concerns.
 */
export function createMapPlacesService(
  store: MapStoreContract = new MapStore()
): MapPlacesService {
  return async (query) => {
    const effectiveZoom = getEffectiveMapZoom(query, query.zoom);
    const { minScore, featuredMinScore } =
      getMapVisibilityThresholds(effectiveZoom);
    const safetyCap = getMapPinsSafetyCap(query.limit);
    const rows = await store.placesInBbox(query, minScore, safetyCap);
    const context: MapRankingContext = { zoom: effectiveZoom };
    const ranked = rankSpatiallyBalancedMapPlaces(
      rows,
      context,
      query,
      safetyCap
    );
    const capHit = rows.length >= safetyCap;

    return {
      places: ranked.map((place, index) => ({
        ...mapPlaceRowToPin(place),
        displayKind:
          (place.map_visibility_score ?? 0) >= featuredMinScore
            ? "featured"
            : "dot",
        displayPriority: index + 1
      })),
      meta: {
        returnedCount: ranked.length,
        limit: safetyCap,
        requestedLimit: query.limit ?? null,
        candidateLimit: safetyCap,
        capped: capHit,
        effectiveZoom,
        minScore,
        featuredMinScore,
        safetyCap,
        capHit,
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
