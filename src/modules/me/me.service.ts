import type { AuthenticatedUser } from "../auth/auth.service.js";
import { getSupabaseClient } from "../../lib/supabase.js";

const PROFILE_COLUMNS = "user_id, display_name, onboarding_status";

type ProfileRow = {
  user_id: string;
  display_name: string | null;
  onboarding_status: string;
};

export type UserProfile = {
  userId: string;
  displayName: string | null;
  onboardingStatus: string;
};

export type MeResult = {
  user: AuthenticatedUser;
  profile: UserProfile;
};

export type ProfileRepository = {
  upsertDefaultProfile: (userId: string) => Promise<UserProfile>;
};

export type MeService = (user: AuthenticatedUser) => Promise<MeResult>;

export const supabaseProfileRepository: ProfileRepository = {
  async upsertDefaultProfile(userId) {
    const { data, error } = await getSupabaseClient()
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
      .single();

    if (error) {
      throw error;
    }

    return mapProfileRow(data as ProfileRow);
  }
};

export function createMeService(
  profileRepository: ProfileRepository = supabaseProfileRepository
): MeService {
  return async (user) => ({
    user,
    profile: await profileRepository.upsertDefaultProfile(user.id)
  });
}

export const getMe = createMeService();

function mapProfileRow(row: ProfileRow): UserProfile {
  return {
    userId: row.user_id,
    displayName: row.display_name,
    onboardingStatus: row.onboarding_status
  };
}
