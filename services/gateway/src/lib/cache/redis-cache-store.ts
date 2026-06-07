import { Redis } from "ioredis";
import type { CacheStore } from "./cache-store.js";

const SCAN_COUNT = 100;

type RedisClient = {
  del(...keys: string[]): Promise<number>;
  get(key: string): Promise<string | null>;
  getBuffer(key: string): Promise<Buffer | null>;
  scan(
    cursor: string,
    match: "MATCH",
    pattern: string,
    count: "COUNT",
    countValue: number
  ): Promise<[string, string[]]>;
  set(
    key: string,
    value: string | Buffer,
    mode: "EX",
    ttlSeconds: number
  ): Promise<"OK" | null>;
};

export class RedisCacheStore implements CacheStore {
  readonly kind = "redis";

  constructor(private readonly redis: RedisClient) {}

  async get<T>(key: string): Promise<T | null> {
    const raw = await this.redis.get(key);

    if (raw === null) {
      return null;
    }

    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }

  async set<T>(key: string, value: T, ttlSeconds: number): Promise<void> {
    await this.redis.set(key, JSON.stringify(value), "EX", ttlSeconds);
  }

  async getBuffer(key: string): Promise<Buffer | null> {
    return this.redis.getBuffer(key);
  }

  async setBuffer(
    key: string,
    value: Buffer,
    ttlSeconds: number
  ): Promise<void> {
    await this.redis.set(key, value, "EX", ttlSeconds);
  }

  async del(key: string): Promise<void> {
    await this.redis.del(key);
  }

  async delByPrefix(prefix: string): Promise<void> {
    let cursor = "0";

    do {
      const [nextCursor, keys] = await this.redis.scan(
        cursor,
        "MATCH",
        `${prefix}*`,
        "COUNT",
        SCAN_COUNT
      );
      cursor = nextCursor;

      if (keys.length > 0) {
        await this.redis.del(...keys);
      }
    } while (cursor !== "0");
  }
}

export function createRedisClient(redisUrl: string) {
  return new Redis(redisUrl, {
    connectTimeout: 500,
    lazyConnect: true,
    maxRetriesPerRequest: 1,
    retryStrategy: () => null
  });
}
