# TASKS 37: Feed — Stable Offset Pagination

**Status: Planned — migration approved.**

Frontend request (iOS, item 2 of 3): `GET /v1/feed/places` cannot be paged. The
client already sends `offset`, but the gateway **silently drops it** (Zod strips
unknown query keys) and always returns the head of the list — so "load more"
re-fetches page 1. The frontend explicitly calls out that the feed is today
**"effectively capped at one batch (max 50)"** and asks to page **until the feed is
exhausted** (their example goes to `offset=60 → ranks 61–90`). So the fix must reach
**past 50**. The client degrades gracefully and stops when a page returns `< limit`.

Gateway-only (schema + one migration). No recommendation-service change.
Independent of `TASKS_36` (already shipped).

## Context

`feedPlacesQuerySchema` ([`feed.schemas.ts:11`](../../src/modules/feed/common/feed.schemas.ts))
has `limit` (1–50, default 20) but **no `offset`**. In `feed.service.ts` the feed
is produced against a snapshot sized `recommendationLimit = Math.min(query.limit *
2, 50)` ([line 128](../../src/modules/feed/services/feed.service.ts)), the raw
recommendation response is cached (10-min TTL, in-memory LRU), and the cards are
always `.slice(0, query.limit)` ([line 217](../../src/modules/feed/services/feed.service.ts)) —
i.e. the head only.

Three things cap the feed at 50 today; all three must lift to page deeper:

- the gateway's own request cap `min(limit*2, 50)` (app-layer);
- the hydration RPC `feed_places_by_source_ids` — `limit least(..., 50)`
  ([`012:85`](../../supabase/migrations/012_feed_places_rpc.sql));
- the fallback RPC `feed_fallback_places` — `limit least(..., 50)`
  ([`012:202`](../../supabase/migrations/012_feed_places_rpc.sql)).

The recommendation service already returns up to `recommend_max_limit=200`. Raising
the two RPC caps to match unlocks a clean, single-call hydration of the whole
snapshot for **both** the personalized and the fallback path — this is the reason a
migration is warranted here (an RPC-body change is exactly what the Migration
Restraint Rule permits when the hardcoded limit is the blocker).

## Decisions

- **Raw `offset` query param** (over a `feedId`/cursor token): the client already
  sends it, it maps 1:1 onto array windowing, and stability comes from the existing
  snapshot cache, not an opaque token.
- **Migration: raise both feed RPC caps `50 → 200`** — approved. Non-destructive
  `create or replace` of the two SQL functions; no table/column/data change.
- **Snapshot decoupled from `limit`/`offset`.** Request a constant
  `FEED_SNAPSHOT_SIZE = 100` from rec and key the cache on **signals + snapshot size
  only** — never on `limit`/`offset`. Every page of the same user hits the **same**
  cached ranked snapshot → stable ordering page-over-page.
- **Hydrate the full snapshot once, compact, then window.** With the RPC cap raised
  we hydrate all ≤100 snapshot rows in one call, drop any missing from the catalog,
  then `.slice(offset, offset + limit)`. Windowing the **compacted** list guarantees
  a full `limit`-sized page until the true end, so a rare missing row never makes the
  client stop early.
- **Global `rank`** = absolute position in the ranked snapshot
  (`offset + localIndex + 1` / the rec rank), not a page-local `index + 1`.
- **End of feed = a short/empty page.** `offset` at/beyond the snapshot → `places:
  []`; the client's "`< limit` → stop" contract handles it. Max depth =
  `FEED_SNAPSHOT_SIZE` (100, tunable to rec's 200).
- **Fallback paginates deep too.** With its RPC cap raised, the anonymous/no-signals
  fallback windows the same way (deterministic `feed_score` order → stable pages).

## Changes (`services/gateway`)

1. **Migration `supabase/migrations/016_feed_rpc_pagination_window.sql`** — non-
   destructive `create or replace` of **both** `feed_places_by_source_ids` and
   `feed_fallback_places`, changing `limit least(greatest(coalesce(result_limit,
   20), 1), 50)` → `... 200`. All column/ordering logic unchanged.

2. **`src/modules/feed/common/feed.schemas.ts`** — add
   `offset: z.coerce.number().int().min(0).default(0)` to `feedPlacesQuerySchema`
   (the `FeedPlacesQuery` component regenerates automatically).

3. **`src/modules/feed/services/feed.service.ts`**
   - add `FEED_SNAPSHOT_SIZE = 100`; replace `recommendationLimit = min(limit*2,
     50)` with the constant for the `personalizedPlaces` request and the hydration
     call;
   - `createRecommendationCacheKey` — drop the per-request `limit`, key on
     `FEED_SNAPSHOT_SIZE` so all pages share one snapshot;
   - hydrate the full snapshot, map to cards with **global rank**, compact, then
     `.slice(query.offset, query.offset + query.limit)`;
   - `fallbackFeed` — fetch `FEED_SNAPSHOT_SIZE` rows and `.slice(offset, offset +
     limit)` with global rank;
   - an out-of-range `offset` yields `places: []` (still `200`, real `feed`/
     `inputSummary` meta).

4. **`src/modules/feed/common/feed.mappers.ts`** — ensure `mapFeedRowToCard`
   emits the **global** rank for the non-recommendation (`context.rank`) path.

5. **Docs** — `docs/FRONTEND_FEED_API.md`: `offset` (int ≥ 0, default 0), the
   cross-page stability guarantee, global `rank`, the empty-page end signal, and the
   ~100 max depth. OpenAPI reflects the new query param.

`src/lib/recommendation-client.ts` needs **no signature change** — it still sends a
numeric `limit`, now the snapshot size.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- **Depth past 50:** with `limit=30`, `offset=60` returns ranks 61–90 (the
  frontend's own example) — not empty, not a repeat of the head — for both the
  personalized and the fallback path.
- **Stability:** page 1 (`offset=0`) and page 2 (`offset=limit`) come from one cached
  snapshot — no overlap, no reordering, `cacheStatus: "hit"` on page 2, contiguous
  global `rank`.
- **End of feed:** `offset` at/beyond the snapshot returns `places: []` (`200`); a
  partial final page has `length < limit`.
- **Cache key** ignores `limit`/`offset`: two different `limit`s for the same user
  reuse the same snapshot (no extra rec call).
- Migration applies on a fresh DB; feeds requested shallower than today are
  unchanged; OpenAPI shows `offset`.

## Dependencies

- **Upstream:** none.
- **Downstream:** none.

## Out Of Scope

Cursor/`feedId` tokens; moving the feed cache to Redis (`TBD_PLATFORM_HARDENING`
concern 7); raising the snapshot beyond rec's 200; changing the recommendation
contract; the photo-list (`TASKS_36`, done) and `googlePlaceId` (item 3, dropped)
requests.
