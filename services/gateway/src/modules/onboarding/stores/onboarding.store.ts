import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  OnboardingStatusValue,
  OnboardingStoreContract
} from "../common/onboarding.types.js";

export class OnboardingStore implements OnboardingStoreContract {
  async setOnboardingStatus(
    userId: string,
    status: OnboardingStatusValue
  ): Promise<void> {
    // Upsert, not update: the profiles row is normally created by GET /v1/me,
    // but nothing guarantees the client called it first — an update would
    // silently write to zero rows and the status would be lost.
    const { error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "upsert",
        name: "profiles_set_onboarding_status"
      },
      async () =>
        getSupabaseClient()
          .from("profiles")
          .upsert(
            {
              user_id: userId,
              onboarding_status: status
            },
            {
              onConflict: "user_id"
            }
          )
    );

    if (error) {
      throw error;
    }
  }
}
