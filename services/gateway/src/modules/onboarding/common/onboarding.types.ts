import type { z } from "zod";
import type {
  onboardingCompleteBodySchema,
  onboardingCompleteResponseSchema,
  onboardingStatusValueSchema
} from "./onboarding.schemas.js";

export type OnboardingStatusValue = z.infer<typeof onboardingStatusValueSchema>;
export type OnboardingCompleteBody = z.infer<typeof onboardingCompleteBodySchema>;
export type OnboardingCompleteResult = z.infer<
  typeof onboardingCompleteResponseSchema
>;

export type OnboardingStoreContract = {
  setOnboardingStatus(
    userId: string,
    status: OnboardingStatusValue
  ): Promise<void>;
};

export type OnboardingServiceContract = {
  completeOnboarding(
    userId: string,
    input: OnboardingCompleteBody
  ): Promise<OnboardingCompleteResult>;
};
