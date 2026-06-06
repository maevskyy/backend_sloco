import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { MeStoreContract, UserProfile } from "../common/me.types.js";

const PROFILE_COLUMNS = "user_id, display_name, onboarding_status";

type ProfileRow = {
  user_id: string;
  display_name: string | null;
  onboarding_status: string;
};

export class MeStore implements MeStoreContract {
  async upsertDefaultProfile(userId: string): Promise<UserProfile> {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "upsert",
        name: "profiles_default"
      },
      async () =>
        getSupabaseClient()
          .from("profiles")
          .upsert(
            {
              user_id: userId
            },
            {
              onConflict: "user_id"
            }
          )
          .select(PROFILE_COLUMNS)
          .single()
    );

    if (error) {
      throw error;
    }

    return mapProfileRow(data as ProfileRow);
  }
}

function mapProfileRow(row: ProfileRow): UserProfile {
  return {
    userId: row.user_id,
    displayName: row.display_name,
    onboardingStatus: row.onboarding_status
  };
}
