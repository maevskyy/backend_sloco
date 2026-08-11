import {
  PlaceNotFoundError,
  savedPlacesService,
  type SavedPlacesServiceContract
} from "../../saved-places/index.js";
import type {
  OnboardingCompleteBody,
  OnboardingCompleteResult,
  OnboardingServiceContract,
  OnboardingStoreContract
} from "../common/onboarding.types.js";
import { OnboardingStore } from "../stores/onboarding.store.js";

export class OnboardingServiceImpl implements OnboardingServiceContract {
  constructor(
    private readonly store: OnboardingStoreContract,
    private readonly savedPlaces: SavedPlacesServiceContract
  ) {}

  async completeOnboarding(
    userId: string,
    input: OnboardingCompleteBody
  ): Promise<OnboardingCompleteResult> {
    // Picks first, status second: if a save blows up (beyond a missing place),
    // the status stays unset and the client can safely retry the whole call —
    // both halves are idempotent.
    let savedCount = 0;

    for (const placeId of dedupe(input.pickedPlaceIds)) {
      try {
        await this.savedPlaces.savePlace(userId, { placeId });
        savedCount += 1;
      } catch (error) {
        // An unknown pick (stale deck, deleted place) is skipped, not a 500.
        if (error instanceof PlaceNotFoundError) {
          continue;
        }

        throw error;
      }
    }

    await this.store.setOnboardingStatus(userId, input.status);

    return {
      onboardingStatus: input.status,
      savedCount
    };
  }
}

function dedupe(placeIds: readonly number[]): number[] {
  return [...new Set(placeIds)];
}

export function createOnboardingService(
  store: OnboardingStoreContract = new OnboardingStore(),
  savedPlaces: SavedPlacesServiceContract = savedPlacesService
): OnboardingServiceContract {
  return new OnboardingServiceImpl(store, savedPlaces);
}

export const onboardingService = createOnboardingService();
