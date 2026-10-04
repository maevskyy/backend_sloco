import type {
  WalkPlannerCallContext,
  WalkPlannerReply
} from "../../../lib/walk-planner-client.js";
import type {
  WalksBody,
  WalksConfigQuery,
  WalksPlaceQuery,
  WalksRouteQuery,
  WalksSearchQuery
} from "./walks.schemas.js";

// What the controller sends back: the service's status (200 or its own
// 4xx/5xx), the camelCase body with placeId added, and Retry-After of a 503.
export type WalksReply = WalkPlannerReply;

export type WalksCallContext = WalkPlannerCallContext;

export type WalksServiceContract = {
  config(query: WalksConfigQuery, context?: WalksCallContext): Promise<WalksReply>;
  plan(
    input: { userId: string | null; body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ): Promise<WalksReply>;
  schedule(
    input: { body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ): Promise<WalksReply>;
  insert(
    input: { body: WalksBody; query: WalksRouteQuery },
    context?: WalksCallContext
  ): Promise<WalksReply>;
  searchPlaces(
    query: WalksSearchQuery,
    context?: WalksCallContext
  ): Promise<WalksReply>;
  place(
    input: { sourceId: string; query: WalksPlaceQuery },
    context?: WalksCallContext
  ): Promise<WalksReply>;
};

export type WalksStoreContract = {
  /** `places.id` per Google CID; CIDs without a row are absent from the map. */
  placeIdsBySourceIds(sourceIds: string[]): Promise<Map<string, number>>;
};

// The user's saved places as taste signals — the feed's own reading of them
// (FeedStore.getUserSignals), so walks and the feed personalise alike.
export type WalksSignalsSource = {
  getUserSignals(userId: string): Promise<{
    favouritesPlaceIds: string[];
    wantToGoPlaceIds: string[];
  }>;
};
