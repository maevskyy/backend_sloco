import type { CacheStore } from "./cache-store.js";

export class NoopCacheStore implements CacheStore {
  readonly kind = "noop";

  async get<T>(): Promise<T | null> {
    return null;
  }

  async set(): Promise<void> {
    return undefined;
  }

  async del(): Promise<void> {
    return undefined;
  }

  async delByPrefix(): Promise<void> {
    return undefined;
  }
}
