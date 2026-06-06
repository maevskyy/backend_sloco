import { AuthStore } from "./stores/auth.store.js";
import type { AuthService } from "./common/auth.types.js";

export type {
  AuthService,
  AuthenticatedUser,
  AuthStoreContract
} from "./common/auth.types.js";

// Default auth service: the Supabase Auth store adapter. DB access lives in the
// store; this module is the shared auth contract used across product modules.
export const supabaseAuthService: AuthService = new AuthStore();

export function extractBearerToken(authorization: unknown): string | null {
  if (typeof authorization !== "string") {
    return null;
  }

  const match = authorization.match(/^Bearer\s+(.+)$/i);
  const token = match?.[1]?.trim();

  return token && token.length > 0 ? token : null;
}
