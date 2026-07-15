# TASKS 35: User Reactions — Feed Integration + Echo

**Status: Planned (awaiting approval).**

Part **3 of 3** of the user-reactions feature. Order: recommendation `TASKS_3`
(contract) → `TASKS_34` (GW storage) → **this (GW feed integration)**. This is where
reactions come alive: favorites strengthen seeds, disliked/hidden places are
excluded from every feed path, and each place echoes its reaction back.

**Depends on both** `TASKS_3` (the rec-service contract accepts
`dislike_place_ids`/`hide_place_ids`) **and** `TASKS_34` (the `place_reactions` table
+ the reactions store).

## Context

`TASKS_34` stores reactions but nothing reads them yet. This task wires them into
the feed: the recommendation request carries the exclusion lists, favorites join
the personalization seeds, and every fallback path (which bypasses the rec service)
filters disliked/hidden places itself. A new reaction must take effect on the next
feed call, which means it has to enter the feed cache key.

## Decisions

- **Exclusion is enforced twice:** engine-side (via `TASKS_3`) for the
  recommendation path, **and** gateway-side for every `*_fallback` path that returns
  places without calling the rec service. For anonymous requests both are no-ops.
- **Favorites strengthen seeds** by joining `favouritesPlaceIds` (no third seed
  tier — the engine only supports two).
- **The feed cache key must include the reaction lists**, otherwise a new reaction
  is invisible until the 10-minute TTL expires.

## Changes (`services/gateway`)

1. **`src/modules/feed/stores/feed.store.ts` — `getSavedSignals` → `getUserSignals`**
   (~lines 57-85). Return four `source_id` lists (reuse the existing `dedupe`
   helper, ~lines 204-218):
   - `favouritesPlaceIds` = explicit favorites ∪ the current derivation (saved not
     in want-to-go, incl. its existing "all saved" fallback); explicit favorites
     first, deduped;
   - `wantToGoPlaceIds` — unchanged;
   - `dislikePlaceIds`, `hidePlaceIds` — from `place_reactions` (reuse the
     `TASKS_34` store).
   Update `FeedSavedSignals` (`feed.types.ts`) and `FeedStoreContract`.

2. **`src/modules/feed/services/feed.service.ts`**
   - (a) The recommendation request body (~lines 133-140) gains
     `dislike_place_ids` and `hide_place_ids`. Mirror the types in
     `src/lib/recommendation-client.ts` and `FeedRecommendationRequest`
     (`feed.types.ts`).
   - (b) `fallbackFeed` (~lines 238-268) — the single choke point for every
     fallback status — filters rows by `dislike ∪ hide` (no-op when there is no
     authenticated user; anonymous behavior unchanged).
   - (c) `createRecommendationCacheKey` (~lines 303-319) — include the dislike/hide
     lists (and the now-extended favourites) in `signalHash`.

3. **Read-side echo** — add `reaction: 'favorite' | 'dislike' | 'hide' | null`:
   - feed card `feedPlaceCardSchema` (`feed.schemas.ts`); default in
     `feed.mappers.ts`; fill it in `enrichFeedSavedState` (`feed.service.ts`
     ~lines 270-288) via the `TASKS_34` store's batch `getReactions`;
   - place detail `placeDetailsSchema` (`places.schemas.ts`); enrich in
     `places.controller.ts` `enrichSavedState`.
   - **⚠️ Cache correctness:** add `reaction` to the strip/default in
     `places.service.ts` `toCachedPlaceDetails` / `CachedPlaceDetails` (Omit),
     otherwise one user's reaction is cached and served to everyone.
   - Wire `reactionsService` into the feed and places modules (`src/app.ts`).
   - Map pins and search results — unchanged.

4. **Docs** — `docs/FRONTEND_FEED_API.md`: the `reaction` field on the feed card
   (the endpoints themselves are documented in `TASKS_34`).

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- Feed integration (mocked rec-service + the fallback paths): a disliked/hidden
  place appears in **no** feed response variant (personalized or any `*_fallback`).
- After a new reaction, the next feed call reflects it (cache key changed).
- A user with only dislikes/hides and no saves still short-circuits to
  `no_signals_fallback` — but filtered.
- Anonymous feed unchanged (no user → filter is a no-op).
- `reaction` echo present on the feed card and place detail; place-detail cache
  does not leak one user's reaction to another. OpenAPI shows the new card field.

## Performance Note

Benchmarks (local, v4, combined catalog): reactions themselves add **~0 ms** — the
exclusion is a candidate-frame filter, and the model is **not** retrained per
request (cold-start is ~40 ms). The real cost is the **pre-existing** v4 personalized
path: ~1–2 s once there are ≥3 seeds (clustering + MMR over ~9k candidates), vs
~60 ms below that. Favorites add seeds, so they push latency toward the high end.
Because a new reaction changes the cache key, each feed refresh **after** a reaction
triggers one such recompute. This is safe for MVP (1–2 s < the gateway's 5 s
timeout, and the 10-min cache absorbs steady state); a dedicated v4 latency
follow-up is tracked separately and is **not** part of this task.

## Dependencies

- **Upstream:** `TASKS_3` (rec contract deployed) **and** `TASKS_34` (table +
  store). Both must land first.
- **Downstream:** none — this completes the feature.

## Out Of Scope

Reaction storage/CRUD (that is `TASKS_34`); `like`; event log; hide-repulsion;
collaborative engines; the v4 latency optimization; any change to saved-places; any
frontend work.
