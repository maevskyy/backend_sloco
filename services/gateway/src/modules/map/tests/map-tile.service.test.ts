import { describe, expect, it } from "vitest";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { createMapTileService } from "../services/map-tile.service.js";
import type { MapTileStoreContract } from "../common/map.tiles.js";

class FakeCacheStore implements CacheStore {
  readonly kind = "redis";
  readonly buffers = new Map<string, Buffer>();

  async get(): Promise<null> {
    return null;
  }

  async set(): Promise<void> {
    return undefined;
  }

  async getBuffer(key: string): Promise<Buffer | null> {
    return this.buffers.get(key) ?? null;
  }

  async setBuffer(
    key: string,
    value: Buffer
  ): Promise<void> {
    this.buffers.set(key, value);
  }

  async del(): Promise<void> {
    return undefined;
  }

  async delByPrefix(): Promise<void> {
    return undefined;
  }
}

describe("MapTileService", () => {
  it("loads a tile from the store and caches it", async () => {
    let calls = 0;
    const tile = Buffer.from([1, 2, 3]);
    const store: MapTileStoreContract = {
      async getTile() {
        calls += 1;
        return tile;
      }
    };
    const cache = new FakeCacheStore();
    const service = createMapTileService(store, cache, 60, 3);
    const params = { z: 13, x: 4501, y: 2899 };

    const first = await service(params);
    const second = await service(params);

    expect(first).toMatchObject({
      statusCode: 200,
      etag: "\"v3\"",
      cacheControl: "public, max-age=31536000, immutable"
    });
    expect(second.statusCode).toBe(200);
    expect(calls).toBe(1);
    expect(cache.buffers.get("tile:v3:13/4501/2899")).toEqual(tile);
  });

  it("returns 204 for an empty tile", async () => {
    const service = createMapTileService(
      {
        async getTile() {
          return Buffer.alloc(0);
        }
      },
      new FakeCacheStore(),
      60,
      1
    );

    await expect(service({ z: 13, x: 4501, y: 2899 })).resolves.toMatchObject({
      statusCode: 204,
      body: null
    });
  });
});
