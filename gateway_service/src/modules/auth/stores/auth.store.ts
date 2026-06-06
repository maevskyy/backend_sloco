import { getSupabaseClient } from "../../../lib/supabase.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import type { AuthStoreContract } from "../common/auth.types.js";

// Supabase Auth adapter: validates a token and returns the authenticated user.
export class AuthStore implements AuthStoreContract {
  async getUserFromToken(token: string) {
    const { data, error } = await measureDependencyMetric(
      {
        dependency: "supabase",
        operation: "auth",
        name: "get_user"
      },
      async () => getSupabaseClient().auth.getUser(token)
    );

    if (error || !data.user) {
      return null;
    }

    return {
      id: data.user.id,
      email: data.user.email ?? null
    };
  }
}
