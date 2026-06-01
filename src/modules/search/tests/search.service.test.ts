import { describe, expect, it } from "vitest";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { SearchPlaceRow, SearchStoreContract } from "../common/search.types.js";
import {
  createSearchPlacesService,
  enrichSearchSavedState
} from "../services/search.service.js";

function searchRow(overrides: Partial<SearchPlaceRow> = {}): SearchPlaceRow {
  return {
    id: 1,
    name: "Seneca Anticafe",
    category: "cafe",
    primary_type: "cafe",
    city: "Bucharest",
    country: "RO",
    formatted_address: null,
    latitude: 44.43,
    longitude: 26.1,
    rating: 4.8,
    price_level: 2,
    primary_photo_path: null,
    primary_photo_url: null,
    primary_photo_width: null,
    primary_photo_height: null,
    primary_photo_source: null,
    distance_m: null,
    match_reason: "name",
    ...overrides
  };
}

function createSavedPlacesService(
  overrides: Partial<SavedPlacesService>
): SavedPlacesService {
  const unused = async () => {
    throw new Error("not used");
  };

  return {
    getSavedDashboard: unused,
    getCollectionDetail: unused,
    savePlace: unused,
    unsavePlace: unused,
    createCollection: unused,
    updateCollection: unused,
    deleteCollection: unused,
    addPlaceToCollection: unused,
    removePlaceFromCollection: unused,
    reorderCollectionPlaces: unused,
    getSavedPlaceIds: unused,
    getSavedPlaceStates: unused,
    ...overrides
  } as SavedPlacesService;
}

describe("search places service", () => {
  it("calls the store and maps rows", async () => {
    const store: SearchStoreContract = {
      async searchPlaces(query) {
        expect(query.q).toBe("coffee");
        expect(query.limit).toBe(20);
        return [
          searchRow({
            id: 42,
            name: "Origo Coffee",
            distance_m: 320,
            match_reason: "category"
          })
        ];
      }
    };
    const service = createSearchPlacesService(store);

    await expect(
      service({
        q: "coffee",
        limit: 20
      })
    ).resolves.toMatchObject({
      query: "coffee",
      places: [
        {
          id: 42,
          name: "Origo Coffee",
          distanceMeters: 320,
          matchReason: "category",
          isSaved: false
        }
      ]
    });
  });

  it("enriches search results with saved state", async () => {
    const savedPlacesService = createSavedPlacesService({
      async getSavedPlaceIds(userId, placeIds) {
        expect(userId).toBe("user-1");
        expect(placeIds).toEqual([1, 2]);
        return new Set([2]);
      }
    });

    await expect(
      enrichSearchSavedState(
        {
          query: "coffee",
          places: [
            {
              ...createSearchPlaceResult(1),
              isSaved: true
            },
            createSearchPlaceResult(2)
          ]
        },
        "user-1",
        savedPlacesService
      )
    ).resolves.toMatchObject({
      places: [
        {
          id: 1,
          isSaved: false
        },
        {
          id: 2,
          isSaved: true
        }
      ]
    });
  });
});

function createSearchPlaceResult(id: number) {
  return {
    id,
    name: `Place ${id}`,
    category: "cafe",
    primaryType: "cafe",
    city: "Bucharest",
    country: "RO",
    formattedAddress: null,
    latitude: 44.43,
    longitude: 26.1,
    rating: null,
    priceLevel: null,
    primaryPhoto: null,
    distanceMeters: null,
    matchReason: "name" as const,
    isSaved: false
  };
}
