import { performance } from "node:perf_hooks";
import { env } from "../../../config/env.js";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { getCacheStore } from "../../../lib/cache/index.js";
import { logCacheMetric } from "../../../observability/metrics.js";
import {
  getMapTileCacheControl,
  getMapTileCacheKey,
  getMapTileEtag,
  type MapTileService,
  type MapTileStoreContract
} from "../common/map.tiles.js";
import { MapTileStore } from "../stores/map-tile.store.js";

const MAP_TILE_CACHE_NAME = "map_tile";

export function createMapTileService(
  store: MapTileStoreContract = new MapTileStore(),
  cacheStore: CacheStore = getCacheStore(),
  ttlSeconds = env.MAP_TILE_CACHE_TTL_SECONDS,
  tileVersion = env.MAP_TILE_VERSION
): MapTileService {
  return async (params) => {
    const cacheKey = getMapTileCacheKey(tileVersion, params);
    const cached = await getCachedTile(cacheStore, cacheKey);
    const body = cached ?? (await store.getTile(params));

    if (!cached) {
      await setCachedTile(cacheStore, cacheKey, body, ttlSeconds);
    }

    const base = {
      etag: getMapTileEtag(tileVersion),
      cacheControl: getMapTileCacheControl()
    };

    if (body.length === 0) {
      return {
        ...base,
        statusCode: 204,
        body: null
      };
    }

    return {
      ...base,
      statusCode: 200,
      body
    };
  };
}

export const getMapTile = createMapTileService();

async function getCachedTile(
  cacheStore: CacheStore,
  key: string
): Promise<Buffer | null> {
  if (cacheStore.kind === "noop") {
    logTileCache("bypass", 0);
    return null;
  }

  const startedAt = performance.now();

  try {
    const cached = await cacheStore.getBuffer(key);
    logTileCache(cached ? "hit" : "miss", elapsedMs(startedAt));
    return cached;
  } catch {
    logTileCache("error", elapsedMs(startedAt));
    return null;
  }
}

async function setCachedTile(
  cacheStore: CacheStore,
  key: string,
  value: Buffer,
  ttlSeconds: number
) {
  if (cacheStore.kind === "noop" || ttlSeconds <= 0) {
    return;
  }

  const startedAt = performance.now();

  try {
    await cacheStore.setBuffer(key, value, ttlSeconds);
    logTileCache("set", elapsedMs(startedAt));
  } catch {
    logTileCache("error", elapsedMs(startedAt));
  }
}

function logTileCache(
  cacheStatus: "bypass" | "error" | "hit" | "miss" | "set",
  durationMs: number
) {
  logCacheMetric({
    cacheName: MAP_TILE_CACHE_NAME,
    cacheStatus,
    durationMs,
    keyPrefix: "tile:v"
  });
}

function elapsedMs(startedAt: number) {
  return Math.round(performance.now() - startedAt);
}
