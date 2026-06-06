import { HealthStore } from "../stores/health.store.js";
import type { HealthService, HealthStoreContract } from "../common/health.types.js";

export type { HealthService } from "../common/health.types.js";

export function createHealthService(
  store: HealthStoreContract = new HealthStore()
): HealthService {
  return {
    checkSupabase: () => store.checkConnection()
  };
}

export const healthService = createHealthService();
