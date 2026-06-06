import { performance } from "node:perf_hooks";
import { env } from "../../../config/env.js";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { getCacheStore } from "../../../lib/cache/index.js";
import { logCacheMetric } from "../../../observability/metrics.js";
import { mapPlaceDetailRow } from "../common/places.mappers.js";
import type {
  PlaceDetails,
  PlaceDetailsService,
  PlacesStoreContract
} from "../common/places.types.js";
import { PlacesStore } from "../stores/places.store.js";

const PLACE_CACHE_NAME = "place_details";
const PLACE_CACHE_PREFIX = "place:v1";

type CachedPlaceDetails = Omit<
  PlaceDetails,
  "isSaved" | "savedCollectionIds"
>;

export function createPlaceDetailsService(
  store: PlacesStoreContract = new PlacesStore(),
  cacheStore: CacheStore = getCacheStore(),
  ttlSeconds = env.PLACE_CACHE_TTL_SECONDS
): PlaceDetailsService {
  return async (placeId) => {
    const cacheKey = getPlaceCacheKey(placeId);
    const cached = await getCachedPlace(cacheStore, cacheKey);

    if (cached) {
      return {
        place: fromCachedPlaceDetails(cached)
      };
    }

    const row = await store.placeDetailsById(placeId);

    if (!row) {
      return null;
    }

    const place = mapPlaceDetailRow(row);

    await setCachedPlace(cacheStore, cacheKey, place, ttlSeconds);

    return {
      place
    };
  };
}

export const getPlaceDetails = createPlaceDetailsService();

function getPlaceCacheKey(placeId: number) {
  return `${PLACE_CACHE_PREFIX}:${placeId}`;
}

async function getCachedPlace(
  cacheStore: CacheStore,
  key: string
): Promise<CachedPlaceDetails | null> {
  if (cacheStore.kind === "noop") {
    logCache("bypass", 0);
    return null;
  }

  const startedAt = performance.now();

  try {
    const cached = await cacheStore.get<CachedPlaceDetails>(key);
    logCache(cached ? "hit" : "miss", elapsedMs(startedAt));
    return cached;
  } catch {
    logCache("error", elapsedMs(startedAt));
    return null;
  }
}

async function setCachedPlace(
  cacheStore: CacheStore,
  key: string,
  place: PlaceDetails,
  ttlSeconds: number
) {
  if (cacheStore.kind === "noop" || ttlSeconds <= 0) {
    return;
  }

  const startedAt = performance.now();

  try {
    await cacheStore.set(key, toCachedPlaceDetails(place), ttlSeconds);
    logCache("set", elapsedMs(startedAt));
  } catch {
    logCache("error", elapsedMs(startedAt));
  }
}

function toCachedPlaceDetails(place: PlaceDetails): CachedPlaceDetails {
  const publicPlace: Partial<PlaceDetails> = { ...place };
  delete publicPlace.isSaved;
  delete publicPlace.savedCollectionIds;

  return publicPlace as CachedPlaceDetails;
}

function fromCachedPlaceDetails(place: CachedPlaceDetails): PlaceDetails {
  return {
    ...place,
    isSaved: false,
    savedCollectionIds: []
  };
}

function logCache(
  cacheStatus: "bypass" | "error" | "hit" | "miss" | "set",
  durationMs: number
) {
  logCacheMetric({
    cacheName: PLACE_CACHE_NAME,
    cacheStatus,
    durationMs,
    keyPrefix: PLACE_CACHE_PREFIX
  });
}

function elapsedMs(startedAt: number) {
  return Math.round(performance.now() - startedAt);
}
