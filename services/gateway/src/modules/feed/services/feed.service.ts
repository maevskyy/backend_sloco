import { createHash } from "node:crypto";
import { bucketsToKeywords, matchesBucketKeywords } from "../../places/index.js";
import { recommendationClient } from "../../../lib/recommendation-client.js";
import {
  reactionsService,
  type ReactionsService
} from "../../reactions/index.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import { savedPlacesService } from "../../saved-places/index.js";
import { mapFeedRowToCard } from "../common/feed.mappers.js";
import type {
  FeedCacheStatus,
  FeedInputSummary,
  FeedPersonalizationStatus,
  FeedPlaceCard,
  FeedPlacesQuery,
  FeedPlacesResult,
  FeedPlacesService,
  FeedRecommendationClient,
  FeedRecommendationResponse,
  FeedUserSignals,
  FeedStoreContract
} from "../common/feed.types.js";
import { FeedStore } from "../stores/feed.store.js";

export type { FeedPlacesService } from "../common/feed.types.js";

const CACHE_TTL_MS = 10 * 60 * 1000;
const MAX_CACHE_ENTRIES = 500;
// 200 = the ceiling everything downstream is provisioned for: migration 016 caps
// both feed RPCs at 200 and the rec-service RECOMMEND_MAX_LIMIT defaults to 200.
// Deeper needs those raised too (TASKS_43).
const FEED_SNAPSHOT_SIZE = 200;
const FALLBACK_ALGORITHM_VERSION = "fallback_visibility_v1";

type CachedRecommendation = {
  response: FeedRecommendationResponse;
  cachedAt: number;
  expiresAt: number;
};

export class FeedRecommendationCache {
  private readonly entries = new Map<string, CachedRecommendation>();

  constructor(
    private readonly ttlMs = CACHE_TTL_MS,
    private readonly maxEntries = MAX_CACHE_ENTRIES
  ) {}

  get(key: string, now = Date.now()) {
    const entry = this.entries.get(key);

    if (!entry) return null;

    if (entry.expiresAt <= now) {
      this.entries.delete(key);
      return null;
    }

    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry;
  }

  set(key: string, response: FeedRecommendationResponse, now = Date.now()) {
    const entry: CachedRecommendation = {
      response,
      cachedAt: now,
      expiresAt: now + this.ttlMs
    };

    this.entries.set(key, entry);
    this.trim();
    return entry;
  }

  clear() {
    this.entries.clear();
  }

  private trim() {
    while (this.entries.size > this.maxEntries) {
      const firstKey = this.entries.keys().next().value;

      if (!firstKey) return;

      this.entries.delete(firstKey);
    }
  }
}

export function createFeedPlacesService(
  store: FeedStoreContract = new FeedStore(),
  client: FeedRecommendationClient = recommendationClient,
  savedService: SavedPlacesService = savedPlacesService,
  cache = new FeedRecommendationCache(),
  reactionsServiceOverride: ReactionsService = reactionsService
): FeedPlacesService {
  return async ({ query, user }) => {
    const generatedAt = Date.now();

    if (!user) {
      return fallbackFeed({
        store,
        query,
        status: "anonymous_fallback",
        cacheStatus: "not_applicable",
        inputSummary: emptyInputSummary(),
        generatedAt,
        savedService,
        reactionsService: reactionsServiceOverride,
        userId: undefined,
        excludedSourceIds: new Set()
      });
    }

    const signals = await store.getUserSignals(user.id);

    if (!hasSignals(signals)) {
      return fallbackFeed({
        store,
        query,
        status: "no_signals_fallback",
        cacheStatus: "not_applicable",
        inputSummary: inputSummaryFromSignals(signals),
        generatedAt,
        savedService,
        reactionsService: reactionsServiceOverride,
        userId: user.id,
        excludedSourceIds: excludedSourceIdsFromSignals(signals)
      });
    }

    const cacheKey = createRecommendationCacheKey(user.id, signals);
    const cached = query.debug ? null : cache.get(cacheKey, generatedAt);
    let cacheStatus: FeedCacheStatus = query.debug ? "bypass" : "hit";
    let cachedOrFresh = cached;

    if (!cachedOrFresh) {
      cacheStatus = query.debug ? "bypass" : "miss";

      try {
        const response = await client.personalizedPlaces({
          user_id: user.id,
          favourites_place_ids: signals.favouritesPlaceIds,
          want_to_go_place_ids: signals.wantToGoPlaceIds,
          dislike_place_ids: signals.dislikePlaceIds,
          hide_place_ids: signals.hidePlaceIds,
          limit: FEED_SNAPSHOT_SIZE,
          exclude_input_places: true,
          debug: query.debug
        });

        cachedOrFresh = query.debug
          ? {
              response,
              cachedAt: generatedAt,
              expiresAt: generatedAt
            }
          : cache.set(cacheKey, response, generatedAt);
      } catch {
        return fallbackFeed({
          store,
          query,
          status: "recommendation_service_fallback",
          cacheStatus,
          inputSummary: inputSummaryFromSignals(signals),
          generatedAt,
          savedService,
          reactionsService: reactionsServiceOverride,
          userId: user.id,
          excludedSourceIds: excludedSourceIdsFromSignals(signals)
        });
      }
    }

    const recommendations = cachedOrFresh.response.recommendations.map(
      (item) => ({
        rank: item.rank,
        sourceId: item.place_id,
        score: item.score
      })
    );

    if (recommendations.length === 0) {
      return fallbackFeed({
        store,
        query,
        status: "empty_recommendation_fallback",
        cacheStatus,
        inputSummary: mapRecommendationInputSummary(
          cachedOrFresh.response.input_summary
        ),
        generatedAt,
        savedService,
        reactionsService: reactionsServiceOverride,
        userId: user.id,
        excludedSourceIds: excludedSourceIdsFromSignals(signals)
      });
    }

    const rows = await store.feedPlacesBySourceIds(
      recommendations.map((item) => item.sourceId),
      query,
      FEED_SNAPSHOT_SIZE
    );
    const recommendationBySourceId = new Map(
      recommendations.map((item) => [item.sourceId, item])
    );
    // The rec engine knows nothing about categories, so the personalized path
    // filters the hydrated rows here — a filtered personalized feed can be
    // shallower than the snapshot (documented in the frontend contract). The
    // fallback path filters inside the RPC instead, keeping full depth.
    const categoryKeywords = query.category
      ? bucketsToKeywords(query.category)
      : null;
    const filteredRows = categoryKeywords
      ? rows.filter((row) =>
          matchesBucketKeywords(categoryKeywords, [
            row.category,
            row.primary_type
          ])
        )
      : rows;
    const snapshot = filteredRows.map((row, index) =>
      mapFeedRowToCard(row, {
        status: "personalized",
        // Under a category filter the recommender's rank has gaps; rank turns
        // positional so offset windows stay contiguous.
        recommendation: categoryKeywords
          ? undefined
          : recommendationBySourceId.get(row.source_id),
        rank: index + 1
      })
    );

    if (snapshot.length === 0) {
      return fallbackFeed({
        store,
        query,
        status: "empty_recommendation_fallback",
        cacheStatus,
        inputSummary: mapRecommendationInputSummary(
          cachedOrFresh.response.input_summary
        ),
        generatedAt,
        savedService,
        reactionsService: reactionsServiceOverride,
        userId: user.id,
        excludedSourceIds: excludedSourceIdsFromSignals(signals)
      });
    }

    const ordered = applySort(snapshot, query.sort);
    const places = ordered.slice(query.offset, query.offset + query.limit);

    return {
      feed: {
        personalizationStatus: "personalized",
        cacheStatus,
        sort: query.sort,
        algorithmVersion: cachedOrFresh.response.algorithm_version,
        embeddingRunId: cachedOrFresh.response.embedding_run_id,
        generatedAt: toIso(cachedOrFresh.cachedAt),
        expiresAt: query.debug ? null : toIso(cachedOrFresh.expiresAt)
      },
      inputSummary: mapRecommendationInputSummary(
        cachedOrFresh.response.input_summary
      ),
      places: await enrichFeedSavedState(
        places,
        user.id,
        savedService,
        reactionsServiceOverride
      )
    };
  };
}

export const getFeedPlaces = createFeedPlacesService();

async function fallbackFeed(input: {
  store: FeedStoreContract;
  query: Parameters<FeedPlacesService>[0]["query"];
  status: FeedPersonalizationStatus;
  cacheStatus: FeedCacheStatus;
  inputSummary: FeedInputSummary;
  generatedAt: number;
  savedService: SavedPlacesService;
  reactionsService: ReactionsService;
  userId: string | undefined;
  excludedSourceIds: Set<string>;
}): Promise<FeedPlacesResult> {
  const rows = await input.store.fallbackFeedPlaces(
    input.query,
    FEED_SNAPSHOT_SIZE,
    input.query.category ? bucketsToKeywords(input.query.category) : null
  );
  const snapshot = rows
    .filter((row) => !input.excludedSourceIds.has(row.source_id))
    .map((row, index) =>
        mapFeedRowToCard(row, {
          status: input.status,
          rank: index + 1
        })
      );
  const places = applySort(snapshot, input.query.sort).slice(
    input.query.offset,
    input.query.offset + input.query.limit
  );

  return {
    feed: {
      personalizationStatus: input.status,
      cacheStatus: input.cacheStatus,
      sort: input.query.sort,
      algorithmVersion: FALLBACK_ALGORITHM_VERSION,
      embeddingRunId: null,
      generatedAt: toIso(input.generatedAt),
      expiresAt: null
    },
    inputSummary: input.inputSummary,
    places: await enrichFeedSavedState(
      places,
      input.userId,
      input.savedService,
      input.reactionsService
    )
  };
}

export async function enrichFeedSavedState(
  places: FeedPlaceCard[],
  userId: string | undefined,
  savedService: SavedPlacesService,
  reactionsServiceOverride: ReactionsService
): Promise<FeedPlaceCard[]> {
  if (!userId || places.length === 0) {
    return places.map(markPlaceAsUnsignedUserState);
  }

  const placeIds = places.map((place) => place.id);
  const [savedPlaceIds, reactionsByPlaceId] = await Promise.all([
    savedService.getSavedPlaceIds(userId, placeIds),
    reactionsServiceOverride.getReactionMap(userId, placeIds)
  ]);

  return places.map((place) => ({
    ...place,
    isSaved: savedPlaceIds.has(place.id),
    reaction: reactionsByPlaceId.get(place.id) ?? null
  }));
}

function markPlaceAsUnsignedUserState(place: FeedPlaceCard): FeedPlaceCard {
  return {
    ...place,
    isSaved: false,
    reaction: null
  };
}

// sort=distance re-orders the whole snapshot BEFORE the offset slice, so offset
// windows continue the distance ordering. Array.prototype.sort is stable, so
// equal distances keep their relevance order; rank becomes positional over the
// sorted snapshot (the client treats rank as positional — FEED_SORT_SPEC).
// Validation guarantees lat/lng, so distanceMeters is set on every row; nulls
// would sort last as a safety net.
function applySort(
  snapshot: FeedPlaceCard[],
  sort: FeedPlacesQuery["sort"]
): FeedPlaceCard[] {
  if (sort !== "distance") {
    return snapshot;
  }

  return [...snapshot]
    .sort(
      (a, b) =>
        (a.distanceMeters ?? Number.POSITIVE_INFINITY) -
        (b.distanceMeters ?? Number.POSITIVE_INFINITY)
    )
    .map((card, index) => ({ ...card, rank: index + 1 }));
}

function hasSignals(signals: FeedUserSignals) {
  return (
    signals.favouritesPlaceIds.length > 0 || signals.wantToGoPlaceIds.length > 0
  );
}

function createRecommendationCacheKey(
  userId: string,
  signals: FeedUserSignals
) {
  const signalHash = createHash("sha256")
    .update(
      JSON.stringify({
        favourites: signals.favouritesPlaceIds,
        wantToGo: signals.wantToGoPlaceIds,
        dislikes: signals.dislikePlaceIds,
        hidden: signals.hidePlaceIds,
        snapshotSize: FEED_SNAPSHOT_SIZE
      })
    )
    .digest("hex")
    .slice(0, 24);

  return `${userId}:${signalHash}:exclude-input`;
}

function inputSummaryFromSignals(signals: FeedUserSignals): FeedInputSummary {
  const validInputIds = new Set([
    ...signals.favouritesPlaceIds,
    ...signals.wantToGoPlaceIds
  ]);

  return {
    favouritesCount: signals.favouritesPlaceIds.length,
    wantToGoCount: signals.wantToGoPlaceIds.length,
    validInputCount: validInputIds.size,
    invalidPlaceIds: []
  };
}

function excludedSourceIdsFromSignals(signals: FeedUserSignals) {
  return new Set([...signals.dislikePlaceIds, ...signals.hidePlaceIds]);
}

function mapRecommendationInputSummary(
  input: FeedRecommendationResponse["input_summary"]
): FeedInputSummary {
  return {
    favouritesCount: input.favourites_count,
    wantToGoCount: input.want_to_go_count,
    validInputCount: input.valid_input_count,
    invalidPlaceIds: input.invalid_place_ids
  };
}

function emptyInputSummary(): FeedInputSummary {
  return {
    favouritesCount: 0,
    wantToGoCount: 0,
    validInputCount: 0,
    invalidPlaceIds: []
  };
}

function toIso(timestampMs: number) {
  return new Date(timestampMs).toISOString();
}
