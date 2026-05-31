import { getSupabaseClient } from "../../../lib/supabase.js";
import type { HealthStoreContract } from "../common/health.types.js";

export class HealthStore implements HealthStoreContract {
  async checkConnection(): Promise<void> {
    const { error } = await getSupabaseClient()
      .from("places")
      .select("id", {
        count: "exact",
        head: true
      })
      .limit(1);

    if (error) {
      throw error;
    }
  }
}
