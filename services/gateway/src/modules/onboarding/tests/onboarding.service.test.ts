import { describe, expect, it } from "vitest";
import {
  PlaceNotFoundError,
  type SavedPlacesService
} from "../../saved-places/index.js";
import type {
  OnboardingStatusValue,
  OnboardingStoreContract
} from "../common/onboarding.types.js";
import { createOnboardingService } from "../services/onboarding.service.js";

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

function createStore(
  calls: Array<{ userId: string; status: OnboardingStatusValue }>
): OnboardingStoreContract {
  return {
    async setOnboardingStatus(userId, status) {
      calls.push({ userId, status });
    }
  };
}

describe("onboarding service", () => {
  it("saves picks as favourites, then writes the status", async () => {
    const order: string[] = [];
    const statusCalls: Array<{ userId: string; status: OnboardingStatusValue }> =
      [];
    const service = createOnboardingService(
      {
        async setOnboardingStatus(userId, status) {
          order.push("status");
          statusCalls.push({ userId, status });
        }
      },
      createSavedPlacesService({
        async savePlace(userId, input) {
          order.push(`save:${input.placeId}`);
          expect(userId).toBe("user-1");
          return {
            placeId: input.placeId,
            isSaved: true,
            collectionIds: [],
            savedAt: "2026-08-12T00:00:00.000Z"
          };
        }
      })
    );

    const result = await service.completeOnboarding("user-1", {
      pickedPlaceIds: [11, 22],
      status: "completed"
    });

    expect(result).toEqual({ onboardingStatus: "completed", savedCount: 2 });
    expect(order).toEqual(["save:11", "save:22", "status"]);
    expect(statusCalls).toEqual([{ userId: "user-1", status: "completed" }]);
  });

  it("dedupes repeated picks", async () => {
    const saved: number[] = [];
    const service = createOnboardingService(
      createStore([]),
      createSavedPlacesService({
        async savePlace(_userId, input) {
          saved.push(input.placeId);
          return {
            placeId: input.placeId,
            isSaved: true,
            collectionIds: [],
            savedAt: "2026-08-12T00:00:00.000Z"
          };
        }
      })
    );

    const result = await service.completeOnboarding("user-1", {
      pickedPlaceIds: [7, 7, 7, 9],
      status: "completed"
    });

    expect(saved).toEqual([7, 9]);
    expect(result.savedCount).toBe(2);
  });

  it("skips unknown places instead of failing", async () => {
    const statusCalls: Array<{ userId: string; status: OnboardingStatusValue }> =
      [];
    const service = createOnboardingService(
      createStore(statusCalls),
      createSavedPlacesService({
        async savePlace(_userId, input) {
          if (input.placeId === 404) {
            throw new PlaceNotFoundError(input.placeId);
          }

          return {
            placeId: input.placeId,
            isSaved: true,
            collectionIds: [],
            savedAt: "2026-08-12T00:00:00.000Z"
          };
        }
      })
    );

    const result = await service.completeOnboarding("user-1", {
      pickedPlaceIds: [1, 404, 2],
      status: "completed"
    });

    expect(result.savedCount).toBe(2);
    expect(statusCalls).toHaveLength(1);
  });

  it("does not write the status when a save fails unexpectedly", async () => {
    const statusCalls: Array<{ userId: string; status: OnboardingStatusValue }> =
      [];
    const service = createOnboardingService(
      createStore(statusCalls),
      createSavedPlacesService({
        async savePlace() {
          throw new Error("supabase down");
        }
      })
    );

    await expect(
      service.completeOnboarding("user-1", {
        pickedPlaceIds: [1],
        status: "completed"
      })
    ).rejects.toThrow("supabase down");
    expect(statusCalls).toHaveLength(0);
  });

  it("writes the status without saves for an empty skip", async () => {
    const statusCalls: Array<{ userId: string; status: OnboardingStatusValue }> =
      [];
    const service = createOnboardingService(
      createStore(statusCalls),
      createSavedPlacesService({})
    );

    const result = await service.completeOnboarding("user-1", {
      pickedPlaceIds: [],
      status: "skipped"
    });

    expect(result).toEqual({ onboardingStatus: "skipped", savedCount: 0 });
    expect(statusCalls).toEqual([{ userId: "user-1", status: "skipped" }]);
  });
});
