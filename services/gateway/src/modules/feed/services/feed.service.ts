import { createHash } from "node:crypto";
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

    const recommendationLimit = Math.min(query.limit * 2, 50);
    const cacheKey = createRecommendationCacheKey(
      user.id,
      signals,
      recommendationLimit
    );
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
          limit: recommendationLimit,
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
      recommendationLimit
    );
    const recommendationBySourceId = new Map(
      recommendations.map((item) => [item.sourceId, item])
    );
    const places = rows
      .map((row, index) =>
        mapFeedRowToCard(row, {
          status: "personalized",
          recommendation: recommendationBySourceId.get(row.source_id),
          rank: index + 1
        })
      )
      .slice(0, query.limit);

    if (places.length === 0) {
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

    return {
      feed: {
        personalizationStatus: "personalized",
        cacheStatus,
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
  const rows = await input.store.fallbackFeedPlaces(input.query, input.query.limit);
  const visibleRows = rows.filter(
    (row) => !input.excludedSourceIds.has(row.source_id)
  );
  const places = visibleRows.map((row, index) =>
    mapFeedRowToCard(row, {
      status: input.status,
      rank: index + 1
    })
  );

  return {
    feed: {
      personalizationStatus: input.status,
      cacheStatus: input.cacheStatus,
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

function hasSignals(signals: FeedUserSignals) {
  return (
    signals.favouritesPlaceIds.length > 0 || signals.wantToGoPlaceIds.length > 0
  );
}

function createRecommendationCacheKey(
  userId: string,
  signals: FeedUserSignals,
  limit: number
) {
  const signalHash = createHash("sha256")
    .update(
      JSON.stringify({
        favourites: signals.favouritesPlaceIds,
        wantToGo: signals.wantToGoPlaceIds,
        dislikes: signals.dislikePlaceIds,
        hidden: signals.hidePlaceIds
      })
    )
    .digest("hex")
    .slice(0, 24);

  return `${userId}:${signalHash}:${limit}:exclude-input`;
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
