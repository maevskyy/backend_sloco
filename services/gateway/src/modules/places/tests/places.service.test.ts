import { describe, expect, it } from "vitest";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { NoopCacheStore } from "../../../lib/cache/noop-cache-store.js";
import { createPlaceDetailsService } from "../services/places.service.js";
import type {
  PlaceDetailRow,
  PlacesStoreContract
} from "../common/places.types.js";

class MemoryCacheStore implements CacheStore {
  readonly kind = "redis";
  private readonly entries = new Map<
    string,
    { expiresAt: number; value: unknown }
  >();

  now = 0;

  async get<T>(key: string): Promise<T | null> {
    const entry = this.entries.get(key);

    if (!entry || entry.expiresAt <= this.now) {
      return null;
    }

    return entry.value as T;
  }

  async set<T>(key: string, value: T, ttlSeconds: number): Promise<void> {
    this.entries.set(key, {
      expiresAt: this.now + ttlSeconds * 1000,
      value
    });
  }

  async del(key: string): Promise<void> {
    this.entries.delete(key);
  }

  async delByPrefix(prefix: string): Promise<void> {
    for (const key of this.entries.keys()) {
      if (key.startsWith(prefix)) {
        this.entries.delete(key);
      }
    }
  }

  peek(key: string) {
    return this.entries.get(key)?.value;
  }
}

class ThrowingCacheStore implements CacheStore {
  readonly kind = "redis";

  async get<T>(): Promise<T | null> {
    throw new Error("redis down");
  }

  async set(): Promise<void> {
    throw new Error("redis down");
  }

  async del(): Promise<void> {
    throw new Error("redis down");
  }

  async delByPrefix(): Promise<void> {
    throw new Error("redis down");
  }
}

function placeRow(overrides: Partial<PlaceDetailRow> = {}): PlaceDetailRow {
  return {
    id: 123,
    source: "google",
    source_id: "ChIJ123",
    name: "Seneca Anticafe",
    category: "cafe",
    latitude: 44.43,
    longitude: 26.1,
    map_visibility_score: 89,
    ...overrides
  } as PlaceDetailRow;
}

function createStore(row: PlaceDetailRow | null): PlacesStoreContract & {
  calls: number;
} {
  return {
    calls: 0,
    async placeDetailsById() {
      this.calls += 1;
      return row;
    }
  };
}

describe("place details service cache", () => {
  it("caches public place details after the first store read", async () => {
    const store = createStore(placeRow());
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 3600);

    const first = await service(123);
    const second = await service(123);

    expect(store.calls).toBe(1);
    expect(first?.place.id).toBe(123);
    expect(second?.place.id).toBe(123);
    expect(second?.place.isSaved).toBe(false);
    expect(second?.place.savedCollectionIds).toEqual([]);
  });

  it("does not store user-specific saved fields in cache", async () => {
    const store = createStore(placeRow());
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 3600);

    await service(123);

    const rawCached = cache.peek("place:v1:123");

    expect(rawCached).not.toHaveProperty("isSaved");
    expect(rawCached).not.toHaveProperty("savedCollectionIds");
  });

  it("reads from store again after cache ttl expires", async () => {
    const store = createStore(placeRow());
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 1);

    await service(123);
    cache.now = 1001;
    await service(123);

    expect(store.calls).toBe(2);
  });

  it("falls back to store when cache is disabled", async () => {
    const store = createStore(placeRow());
    const service = createPlaceDetailsService(store, new NoopCacheStore(), 3600);

    await service(123);
    await service(123);

    expect(store.calls).toBe(2);
  });

  it("does not cache missing places", async () => {
    const store = createStore(null);
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 3600);

    await service(999);
    await service(999);

    expect(store.calls).toBe(2);
    expect(cache.peek("place:v1:999")).toBeUndefined();
  });

  it("falls back to store when redis operations fail", async () => {
    const store = createStore(placeRow());
    const service = createPlaceDetailsService(
      store,
      new ThrowingCacheStore(),
      3600
    );

    const result = await service(123);

    expect(result?.place.id).toBe(123);
    expect(store.calls).toBe(1);
  });
});
