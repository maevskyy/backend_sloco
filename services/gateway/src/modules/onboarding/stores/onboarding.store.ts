import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type {
  OnboardingStatusValue,
  OnboardingStoreContract
} from "../common/onboarding.types.js";

export class OnboardingStore implements OnboardingStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async setOnboardingStatus(
    userId: string,
    status: OnboardingStatusValue
  ): Promise<void> {
    // Upsert, not update: the profiles row is normally created by GET /v1/me,
    // but nothing guarantees the client called it first — an update would
    // silently write to zero rows and the status would be lost. Only
    // onboarding_status is overwritten on conflict, as with the PostgREST
    // upsert payload.
    await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "upsert",
        name: "profiles_set_onboarding_status"
      },
      async () =>
        this.db.query(
          `insert into public.profiles (user_id, onboarding_status)
           values ($1, $2)
           on conflict (user_id) do update
             set onboarding_status = excluded.onboarding_status`,
          [userId, status]
        )
    );
  }
}
