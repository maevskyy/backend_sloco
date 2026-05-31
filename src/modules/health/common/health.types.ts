export type HealthStoreContract = {
  checkConnection(): Promise<void>;
};

export type HealthService = {
  checkSupabase(): Promise<void>;
};
