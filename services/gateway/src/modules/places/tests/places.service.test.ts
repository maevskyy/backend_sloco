import { describe, expect, it } from "vitest";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { NoopCacheStore } from "../../../lib/cache/noop-cache-store.js";
import { createPlaceDetailsService } from "../services/places.service.js";
import type {
  PlaceDetailRow,
  PlacePhotoRow,
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

  async getBuffer(): Promise<Buffer | null> {
    return null;
  }

  async set<T>(key: string, value: T, ttlSeconds: number): Promise<void> {
    this.entries.set(key, {
      expiresAt: this.now + ttlSeconds * 1000,
      value
    });
  }

  async setBuffer(): Promise<void> {
    return undefined;
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

  async getBuffer(): Promise<Buffer | null> {
    throw new Error("redis down");
  }

  async set(): Promise<void> {
    throw new Error("redis down");
  }

  async setBuffer(): Promise<void> {
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

function photoRow(overrides: Partial<PlacePhotoRow> = {}): PlacePhotoRow {
  return {
    storage_path: "google/ChIJ123/vibe/photo-1.jpg",
    public_url: "https://r2.example.com/google/ChIJ123/vibe/photo-1.jpg",
    width: 1200,
    height: 900,
    photo_source: "vibe",
    ...overrides
  };
}

function createStore(row: PlaceDetailRow | null): PlacesStoreContract & {
  calls: number;
  photoCalls: number;
} {
  return {
    calls: 0,
    photoCalls: 0,
    async placeDetailsById() {
      this.calls += 1;
      return row;
    },
    async placePhotos() {
      this.photoCalls += 1;
      return [photoRow()];
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
    expect(store.photoCalls).toBe(1);
    expect(first?.place.id).toBe(123);
    expect(first?.place.photos).toEqual([
      {
        path: "google/ChIJ123/vibe/photo-1.jpg",
        url: "https://r2.example.com/google/ChIJ123/vibe/photo-1.jpg",
        width: 1200,
        height: 900,
        source: "vibe"
      }
    ]);
    expect(second?.place.id).toBe(123);
    expect(second?.place.isSaved).toBe(false);
    expect(second?.place.savedCollectionIds).toEqual([]);
    expect(second?.place.reaction).toBeNull();
    expect(second?.place.photos).toEqual(first?.place.photos);
  });

  it("does not store user-specific saved fields in cache", async () => {
    const store = createStore(placeRow());
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 3600);

    await service(123);

    const rawCached = cache.peek("place:v1:123");

    expect(rawCached).not.toHaveProperty("isSaved");
    expect(rawCached).not.toHaveProperty("savedCollectionIds");
    expect(rawCached).not.toHaveProperty("reaction");
    expect(rawCached).toHaveProperty("photos");
  });

  it("reads from store again after cache ttl expires", async () => {
    const store = createStore(placeRow());
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 1);

    await service(123);
    cache.now = 1001;
    await service(123);

    expect(store.calls).toBe(2);
    expect(store.photoCalls).toBe(2);
  });

  it("falls back to store when cache is disabled", async () => {
    const store = createStore(placeRow());
    const service = createPlaceDetailsService(store, new NoopCacheStore(), 3600);

    await service(123);
    await service(123);

    expect(store.calls).toBe(2);
    expect(store.photoCalls).toBe(2);
  });

  it("does not cache missing places", async () => {
    const store = createStore(null);
    const cache = new MemoryCacheStore();
    const service = createPlaceDetailsService(store, cache, 3600);

    await service(999);
    await service(999);

    expect(store.calls).toBe(2);
    expect(store.photoCalls).toBe(0);
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
    expect(store.photoCalls).toBe(1);
  });

  it("defaults to an empty photo list when the place has no extra photos", async () => {
    const store = {
      ...createStore(placeRow()),
      async placePhotos() {
        this.photoCalls += 1;
        return [];
      }
    };
    const service = createPlaceDetailsService(store, new NoopCacheStore(), 3600);

    const result = await service(123);

    expect(result?.place.photos).toEqual([]);
    expect(store.photoCalls).toBe(1);
  });
});
