import { getSupabaseClient } from "../../lib/supabase.js";

export type AuthenticatedUser = {
  id: string;
  email: string | null;
};

export type AuthService = {
  getUserFromToken: (token: string) => Promise<AuthenticatedUser | null>;
};

export const supabaseAuthService: AuthService = {
  async getUserFromToken(token) {
    const { data, error } = await getSupabaseClient().auth.getUser(token);

    if (error || !data.user) {
      return null;
    }

    return {
      id: data.user.id,
      email: data.user.email ?? null
    };
  }
};

export function extractBearerToken(authorization: unknown) {
  if (typeof authorization !== "string") {
    return null;
  }

  const match = authorization.match(/^Bearer\s+(.+)$/i);
  const token = match?.[1]?.trim();

  return token && token.length > 0 ? token : null;
}
