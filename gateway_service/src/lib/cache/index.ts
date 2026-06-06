import { env } from "../../config/env.js";
import type { CacheStore } from "./cache-store.js";
import { NoopCacheStore } from "./noop-cache-store.js";
import { createRedisClient, RedisCacheStore } from "./redis-cache-store.js";

let cacheStore: CacheStore | null = null;

export function getCacheStore(): CacheStore {
  if (cacheStore) {
    return cacheStore;
  }

  if (!env.REDIS_URL || env.PLACE_CACHE_TTL_SECONDS === 0) {
    cacheStore = new NoopCacheStore();
    return cacheStore;
  }

  cacheStore = new RedisCacheStore(createRedisClient(env.REDIS_URL));
  return cacheStore;
}
