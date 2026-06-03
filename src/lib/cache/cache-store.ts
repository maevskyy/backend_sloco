export type CacheStoreKind = "noop" | "redis";

export type CacheStatus = "bypass" | "error" | "hit" | "miss" | "set";

export type CacheMetricInput = {
  cacheName: string;
  cacheStatus: CacheStatus;
  durationMs: number;
  keyPrefix: string;
};

export interface CacheStore {
  readonly kind: CacheStoreKind;
  get<T>(key: string): Promise<T | null>;
  set<T>(key: string, value: T, ttlSeconds: number): Promise<void>;
  del(key: string): Promise<void>;
  delByPrefix(prefix: string): Promise<void>;
}
