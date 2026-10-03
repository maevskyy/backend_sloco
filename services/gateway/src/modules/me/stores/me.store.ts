import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { MeStoreContract, UserProfile } from "../common/me.types.js";

type ProfileRow = {
  user_id: string;
  display_name: string | null;
  onboarding_status: string;
};

export class MeStore implements MeStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async upsertDefaultProfile(userId: string): Promise<UserProfile> {
    // The PostgREST upsert wrote only user_id: an existing profile keeps its
    // display_name / onboarding_status. The no-op DO UPDATE (not DO NOTHING)
    // makes RETURNING yield the existing row too.
    const result = await measureDependencyMetric(
      {
        dependency: "postgres",
        operation: "upsert",
        name: "profiles_default"
      },
      async () =>
        this.db.query<ProfileRow>(
          `insert into public.profiles (user_id)
           values ($1)
           on conflict (user_id) do update
             set user_id = excluded.user_id
           returning user_id, display_name, onboarding_status`,
          [userId]
        )
    );

    // .single() semantics: exactly one row or an error.
    const [row] = result.rows;

    if (!row || result.rows.length !== 1) {
      throw new Error(
        `profiles upsert returned ${result.rows.length} rows, expected 1`
      );
    }

    return mapProfileRow(row);
  }
}

function mapProfileRow(row: ProfileRow): UserProfile {
  return {
    userId: row.user_id,
    displayName: row.display_name,
    onboardingStatus: row.onboarding_status
  };
}
