import { mapPlaceDetailRow } from "../common/places.mappers.js";
import type {
  PlaceDetailsService,
  PlacesStoreContract
} from "../common/places.types.js";
import { PlacesStore } from "../stores/places.store.js";

export function createPlaceDetailsService(
  store: PlacesStoreContract = new PlacesStore()
): PlaceDetailsService {
  return async (placeId) => {
    const row = await store.placeDetailsById(placeId);

    return row
      ? {
          place: mapPlaceDetailRow(row)
        }
      : null;
  };
}

export const getPlaceDetails = createPlaceDetailsService();
