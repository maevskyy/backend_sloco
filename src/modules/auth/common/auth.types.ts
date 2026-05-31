export type AuthenticatedUser = {
  id: string;
  email: string | null;
};

export type AuthService = {
  getUserFromToken: (token: string) => Promise<AuthenticatedUser | null>;
};

// auth has no business logic beyond the Supabase Auth adapter, so the store
// contract is the same shape as the service contract.
export type AuthStoreContract = AuthService;
