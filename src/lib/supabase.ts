import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { env } from "../config/env.js";

let supabaseClient: SupabaseClient | null = null;

export function getSupabaseClient() {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_ROLE_KEY) {
    throw new Error("Supabase environment variables are not configured");
  }

  supabaseClient ??= createClient(
    env.SUPABASE_URL,
    env.SUPABASE_SERVICE_ROLE_KEY,
    {
      auth: {
        autoRefreshToken: false,
        persistSession: false
      }
    }
  );

  return supabaseClient;
}

export async function checkSupabaseConnection() {
  const { error } = await getSupabaseClient()
    .from("raw_tripadvisor_restaurants")
    .select("id", {
      count: "exact",
      head: true
    })
    .limit(1);

  if (error) {
    throw error;
  }
}
