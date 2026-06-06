import { MeStore } from "../stores/me.store.js";
import type { MeService, MeStoreContract } from "../common/me.types.js";

export type { MeService } from "../common/me.types.js";

export function createMeService(
  store: MeStoreContract = new MeStore()
): MeService {
  return async (user) => ({
    user,
    profile: await store.upsertDefaultProfile(user.id)
  });
}

export const getMe = createMeService();
