import { z } from "zod";

// Single source of truth for the onboarding contract (TASKS_38).
// `not_started` is the profiles-row default; this endpoint is the only writer
// and can only move the status forward to one of these two values.
export const onboardingStatusValueSchema = z.enum(["completed", "skipped"]);

export const onboardingCompleteBodySchema = z.object({
  // The places the user "liked" during the onboarding deck. Saved as plain
  // saved_places rows, which the feed counts as favourites — so finishing
  // onboarding is what flips the user onto the personalized path. Empty is
  // valid (a skip, or a completed flow with no picks).
  pickedPlaceIds: z.array(z.number().int().min(1)).max(100),
  status: onboardingStatusValueSchema
});

export const onboardingCompleteResponseSchema = z.object({
  onboardingStatus: onboardingStatusValueSchema,
  // Picks that were actually saved; unknown place ids are skipped, not errors.
  savedCount: z.number().int().min(0)
});

export const onboardingSchemaRegistry = z.registry<{ id: string }>();

onboardingSchemaRegistry.add(onboardingStatusValueSchema, {
  id: "OnboardingStatusValue"
});
onboardingSchemaRegistry.add(onboardingCompleteBodySchema, {
  id: "OnboardingCompleteBody"
});
onboardingSchemaRegistry.add(onboardingCompleteResponseSchema, {
  id: "OnboardingCompleteResponse"
});
