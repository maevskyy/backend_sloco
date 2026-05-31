import type { AuthenticatedUser } from "../../auth/auth.service.js";

export type UserProfile = {
  userId: string;
  displayName: string | null;
  onboardingStatus: string;
};

export type MeResult = {
  user: AuthenticatedUser;
  profile: UserProfile;
};

export type MeStoreContract = {
  upsertDefaultProfile(userId: string): Promise<UserProfile>;
};

export type MeService = (user: AuthenticatedUser) => Promise<MeResult>;
