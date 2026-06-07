import { describe, expect, it } from "vitest";
import { RedisCacheStore } from "./redis-cache-store.js";

class FakeRedis {
  readonly data = new Map<string, string | Buffer>();
  readonly deleted: string[] = [];
  private scanSnapshot: string[] = [];

  async get(key: string) {
    const value = this.data.get(key);
    return typeof value === "string" ? value : null;
  }

  async getBuffer(key: string) {
    const value = this.data.get(key);
    return Buffer.isBuffer(value) ? value : null;
  }

  async set(...args: [string, string | Buffer, "EX", number]) {
    const [key, value] = args;

    this.data.set(key, value);
    return "OK" as const;
  }

  async del(...keys: string[]) {
    this.deleted.push(...keys);

    for (const key of keys) {
      this.data.delete(key);
    }

    return keys.length;
  }

  async scan(
    cursor: string,
    _match: "MATCH",
    pattern: string,
    count: "COUNT",
    countValue: number
  ): Promise<[string, string[]]> {
    expect(count).toBe("COUNT");
    expect(countValue).toBeGreaterThan(0);

    if (cursor === "0") {
      const prefix = pattern.replace("*", "");
      this.scanSnapshot = [...this.data.keys()].filter((key) =>
        key.startsWith(prefix)
      );

      return ["1", this.scanSnapshot.slice(0, 1)];
    }

    return ["0", this.scanSnapshot.slice(1)];
  }
}

describe("RedisCacheStore", () => {
  it("serializes values through JSON", async () => {
    const redis = new FakeRedis();
    const cache = new RedisCacheStore(redis);

    await cache.set("place:v1:1", { id: 1, name: "Place" }, 60);

    expect(await cache.get("place:v1:1")).toEqual({
      id: 1,
      name: "Place"
    });
  });

  it("stores binary buffers without JSON serialization", async () => {
    const redis = new FakeRedis();
    const cache = new RedisCacheStore(redis);
    const tile = Buffer.from([1, 2, 3]);

    await cache.setBuffer("tile:v1:1/2/3", tile, 60);

    expect(await cache.getBuffer("tile:v1:1/2/3")).toEqual(tile);
    expect(await cache.get("tile:v1:1/2/3")).toBeNull();
  });

  it("treats invalid JSON as cache miss", async () => {
    const redis = new FakeRedis();
    redis.data.set("place:v1:1", "{broken");
    const cache = new RedisCacheStore(redis);

    expect(await cache.get("place:v1:1")).toBeNull();
  });

  it("deletes by prefix with scan batches", async () => {
    const redis = new FakeRedis();
    redis.data.set("place:v1:1", "{}");
    redis.data.set("place:v1:2", "{}");
    redis.data.set("search:v1:x", "{}");
    const cache = new RedisCacheStore(redis);

    await cache.delByPrefix("place:v1:");

    expect(redis.deleted).toEqual(["place:v1:1", "place:v1:2"]);
    expect(redis.data.has("search:v1:x")).toBe(true);
  });
});
