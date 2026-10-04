import { performance } from "node:perf_hooks";
import type { CacheStore } from "../../../lib/cache/cache-store.js";
import { NoopCacheStore } from "../../../lib/cache/noop-cache-store.js";
import { logCacheMetric } from "../../../observability/metrics.js";
import {
  walkPlannerClient,
  type WalkPlannerClient,
  type WalkPlannerReply
} from "../../../lib/walk-planner-client.js";
import { toCamel, toSnake } from "../common/walks.keys.js";
import type {
  WalksBody,
  WalksConfigQuery,
  WalksPlaceQuery,
  WalksRouteQuery,
  WalksSearchQuery
} from "../common/walks.schemas.js";
import type {
  WalksCallContext,
  WalksReply,
  WalksServiceContract,
  WalksSignalsSource,
  WalksStoreContract
} from "../common/walks.types.js";

// The service caps favourites + want-to-go at 500 ids (more is a 422) and
// uses the first 200 as taste seeds, favourites first.
const MAX_PERSONAL_IDS = 500;

// Favourites come from the user's saved places on the server; topK and debug
// are research-bench options. None of them is the app's to send.
const SERVER_OWNED_PLAN_KEYS = [
  "favourite_place_ids",
  "want_to_go_place_ids",
  "top_k",
  "debug"
];

// Shared across users: neither depends on who asks. Config changes only with
// a service redeploy (new bundle or version) and a card's placeId only when
// places rows are added, so a stale entry lives at most one TTL after that.
// Plans and edits are never cached: they depend on favourites and location.
const CACHE_PREFIX = "walks:v1";
const CONFIG_CACHE_TTL_SECONDS = 600;
const PLACE_CACHE_TTL_SECONDS = 3600;

type JsonObject = Record<string, unknown>;

// Where a camelCase response carries the stops and cards that get `placeId`
// (never the request echo or sequence items: they round-trip to the service).
type CardPicker = (body: JsonObject) => JsonObject[];

export class WalksServiceImpl implements WalksServiceContract {
  constructor(
    private readonly client: WalkPlannerClient,
    private readonly store: WalksStoreContract,
    private readonly signals: WalksSignalsSource,
    private readonly cacheStore: CacheStore = new NoopCacheStore()
  ) {}

  async config(query: WalksConfigQuery, context?: WalksCallContext) {
    return this.cached(
      "config",
      cacheKey("config", query.city, query.lang),
      CONFIG_CACHE_TTL_SECONDS,
      async () =>
        this.toApp(
          await this.client.config(serviceQuery(query), context),
          () => []
        )
    );
  }

  async plan(
    input: { userId: string | null; body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ) {
    const body = toSnake(input.body) as JsonObject;

    for (const key of SERVER_OWNED_PLAN_KEYS) {
      delete body[key];
    }

    if (input.userId) {
      Object.assign(body, await this.personalIds(input.userId));
    }

    const reply = await this.client.plan(
      body,
      serviceQuery(input.query),
      context
    );
    return this.toApp(reply, (camel) => [
      ...cardsOf(camel.places),
      ...arrayOf(camel.variants).flatMap((variant) => arrayOf(variant.stops))
    ]);
  }

  // Edits pass the request echo through untouched — favourites included: an
  // edit reports the stops' interest with the same taste as the plan.
  async schedule(
    input: { body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ) {
    const reply = await this.client.schedule(
      toSnake(input.body),
      serviceQuery(input.query),
      context
    );
    return this.toApp(reply, editCards);
  }

  async insert(
    input: { body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ) {
    const reply = await this.client.insert(
      toSnake(input.body),
      serviceQuery(input.query),
      context
    );
    return this.toApp(reply, editCards);
  }

  async searchPlaces(query: WalksSearchQuery, context?: WalksCallContext) {
    const reply = await this.client.searchPlaces(serviceQuery(query), context);
    return this.toApp(reply, (camel) => arrayOf(camel.results));
  }

  async place(
    input: { sourceId: string; query: WalksPlaceQuery },
    context?: WalksCallContext
  ) {
    return this.cached(
      "place",
      cacheKey("place", input.sourceId, input.query.city, input.query.lang),
      PLACE_CACHE_TTL_SECONDS,
      async () =>
        this.toApp(
          await this.client.place(
            input.sourceId,
            serviceQuery(input.query),
            context
          ),
          (camel) => [camel]
        )
    );
  }

  // Cache-aside for a 200 answer only; a cache failure never fails the call.
  private async cached(
    name: string,
    key: string,
    ttlSeconds: number,
    produce: () => Promise<WalksReply>
  ): Promise<WalksReply> {
    const cacheName = `walks_${name}`;

    if (this.cacheStore.kind === "noop") {
      logCache(cacheName, "bypass", 0);
      return produce();
    }

    let startedAt = performance.now();

    try {
      const body = await this.cacheStore.get<unknown>(key);

      if (body !== null) {
        logCache(cacheName, "hit", elapsedMs(startedAt));
        return { status: 200, body };
      }

      logCache(cacheName, "miss", elapsedMs(startedAt));
    } catch {
      logCache(cacheName, "error", elapsedMs(startedAt));
    }

    const reply = await produce();

    if (reply.status === 200) {
      startedAt = performance.now();

      try {
        await this.cacheStore.set(key, reply.body, ttlSeconds);
        logCache(cacheName, "set", elapsedMs(startedAt));
      } catch {
        logCache(cacheName, "error", elapsedMs(startedAt));
      }
    }

    return reply;
  }

  private async personalIds(userId: string) {
    const signals = await this.signals.getUserSignals(userId);
    const favourites = uniqueIds(signals.favouritesPlaceIds).slice(
      0,
      MAX_PERSONAL_IDS
    );
    const favouriteSet = new Set(favourites);
    // A place in both lists counts as a favourite.
    const wantToGo = uniqueIds(signals.wantToGoPlaceIds)
      .filter((id) => !favouriteSet.has(id))
      .slice(0, MAX_PERSONAL_IDS - favourites.length);

    return {
      ...(favourites.length > 0 ? { favourite_place_ids: favourites } : {}),
      ...(wantToGo.length > 0 ? { want_to_go_place_ids: wantToGo } : {})
    };
  }

  private async toApp(
    reply: WalkPlannerReply,
    pickCards: CardPicker
  ): Promise<WalksReply> {
    const body = toCamel(reply.body);

    if (reply.status === 200 && isObject(body)) {
      await this.addPlaceIds(pickCards(body));
    }

    return { ...reply, body };
  }

  // One batched lookup per response; a CID without a places row (≈7 % of the
  // Bucharest walk catalog) gets placeId null — the card still renders from
  // the walk payload, only saving it is off.
  private async addPlaceIds(cards: JsonObject[]) {
    const withSourceId = cards.filter(
      (card) => typeof card.sourceId === "string"
    );

    if (withSourceId.length === 0) return;

    const placeIds = await this.store.placeIdsBySourceIds([
      ...new Set(withSourceId.map((card) => card.sourceId as string))
    ]);

    for (const card of withSourceId) {
      card.placeId = placeIds.get(card.sourceId as string) ?? null;
    }
  }
}

export function createWalksService(
  store: WalksStoreContract,
  signals: WalksSignalsSource,
  cacheStore?: CacheStore,
  client: WalkPlannerClient = walkPlannerClient
) {
  return new WalksServiceImpl(client, store, signals, cacheStore);
}

// Absent options become "_" so `?lang=` and no lang share one entry.
function cacheKey(kind: string, ...parts: Array<string | undefined>) {
  return [CACHE_PREFIX, kind, ...parts.map((part) => part?.toLowerCase() ?? "_")].join(":");
}

function logCache(
  cacheName: string,
  cacheStatus: "bypass" | "error" | "hit" | "miss" | "set",
  durationMs: number
) {
  logCacheMetric({ cacheName, cacheStatus, durationMs, keyPrefix: CACHE_PREFIX });
}

function elapsedMs(startedAt: number) {
  return Math.round(performance.now() - startedAt);
}

function editCards(camel: JsonObject) {
  const variant = isObject(camel.variant) ? camel.variant : {};
  return [...cardsOf(camel.places), ...arrayOf(variant.stops)];
}

// App query (camelCase) → service query (snake_case): includeClosed →
// include_closed; absent options stay absent.
function serviceQuery(query: object) {
  return toSnake(query) as Record<
    string,
    string | number | boolean | undefined
  >;
}

function cardsOf(places: unknown) {
  return isObject(places) ? Object.values(places).filter(isObject) : [];
}

function arrayOf(value: unknown) {
  return Array.isArray(value) ? value.filter(isObject) : [];
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function uniqueIds(ids: readonly unknown[]) {
  return [
    ...new Set(ids.filter((id): id is string => typeof id === "string"))
  ];
}
