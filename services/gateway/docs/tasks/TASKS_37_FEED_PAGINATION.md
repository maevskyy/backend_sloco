# TASKS 37: Feed — Stable Offset Pagination

**Status: Planned (awaiting approval).**

Frontend request (iOS, item 2 of 3): `GET /v1/feed/places` cannot be paged. The
client already sends `offset`, but the gateway **silently drops it** (Zod strips
unknown query keys) and always returns the head of the list — so "load more"
re-fetches page 1. The client degrades gracefully and stops when a page returns
`< limit`.

Gateway-only. No recommendation-service change. Independent of `TASKS_36`.

## Context

`feedPlacesQuerySchema` ([`feed.schemas.ts:11`](../../src/modules/feed/common/feed.schemas.ts))
has `limit` (1–50, default 20) but **no `offset`**. In `feed.service.ts` the feed
is produced against a snapshot sized `recommendationLimit = Math.min(query.limit *
2, 50)` ([line 128](../../src/modules/feed/services/feed.service.ts)), the raw
recommendation response is cached (10-min TTL, in-memory LRU), and the cards are
always `.slice(0, query.limit)` ([line 217](../../src/modules/feed/services/feed.service.ts)) —
i.e. the head only.

Two facts make stable paging straightforward:

- The recommendation snapshot is **already cached** and the rec ranking is
  deterministic. The hydration RPC `feed_places_by_source_ids`
  ([migration `012`](../../supabase/migrations/012_feed_places_rpc.sql)) **preserves
  rec order** (`order by r.input_rank asc`), and the fallback RPC
  `feed_fallback_places` is deterministically ordered by `feed_score`. So "page 2
  == same ranking as page 1" comes for free **if the cache key does not change
  between pages**.
- The snapshot is currently capped at 50 in **two** places: the gateway
  (`min(limit*2, 50)`) **and** the RPCs themselves (`limit least(..., 50)` at
  [`012:85`](../../supabase/migrations/012_feed_places_rpc.sql) and
  [`012:202`](../../supabase/migrations/012_feed_places_rpc.sql)). Paging deeper
  than 50 needs both caps raised.

## Decisions

- **Raw `offset` query param** (chosen over a `feedId`/cursor token): the client
  already sends it, it maps 1:1 onto array windowing, and stability is guaranteed
  by the existing snapshot cache rather than by an opaque token. A cursor buys
  nothing here for MVP.
- **Fixed snapshot, decoupled from `limit`/`offset`.** Request a constant
  `FEED_SNAPSHOT_SIZE` (100) from the rec service and key the cache on **signals +
  snapshot size only** — never on `limit` or `offset`. Every page of the same user
  hits the **same** cached ranked snapshot → stable ordering across pages. This is
  the core of the task.
- **Window the hydrated, compacted list.** Hydrate the whole snapshot once (≤100
  rows, one RPC), drop places missing from the catalog, then `slice(offset, offset
  + limit)`. Windowing the **compacted** list guarantees a full `limit`-sized page
  until the true end, so a rare missing row does not make the client stop early.
- **Global `rank`.** `rank` becomes the place's absolute position in the ranked
  snapshot (`offset + localIndex + 1` / the rec rank), not a page-local `index + 1`.
  Stable and meaningful across pages.
- **End of feed = a short/empty page.** When `offset` is at/beyond the snapshot,
  return `places: []`; the client's existing "`< limit` → stop" contract handles
  it. Max paging depth is `FEED_SNAPSHOT_SIZE`; document it.

## Changes (`services/gateway`)

1. **Migration `supabase/migrations/016_feed_rpc_pagination_window.sql`** — non-
   destructive `create or replace` of **both** `feed_places_by_source_ids` and
   `feed_fallback_places`, raising the `limit least(..., 50)` cap to **200** (rec
   service max). Ordering clauses unchanged (already stable). No column/data change.

2. **`src/modules/feed/common/feed.schemas.ts`** — add
   `offset: z.coerce.number().int().min(0).default(0)` to `feedPlacesQuerySchema`
   (the `FeedPlacesQuery` component regenerates automatically).

3. **`src/modules/feed/services/feed.service.ts`**
   - add `FEED_SNAPSHOT_SIZE = 100`; replace `recommendationLimit = min(limit*2,
     50)` with the constant for **both** the `personalizedPlaces` request and the
     hydration call;
   - `createRecommendationCacheKey` — drop the per-request `limit`, key on
     `FEED_SNAPSHOT_SIZE` (constant) so all pages share one snapshot;
   - hydrate the snapshot, map to cards with **global rank**, compact, then
     `.slice(query.offset, query.offset + query.limit)`;
   - `fallbackFeed` — fetch `offset + limit` deterministically-ordered rows (bounded
     by the raised cap) and `.slice(offset, offset + limit)` with global rank;
   - an out-of-range `offset` yields `places: []` (still `200`, real `feed`/
     `inputSummary` meta).

4. **`src/modules/feed/common/feed.mappers.ts`** — ensure `mapFeedRowToCard`
   receives/emits the **global** rank for the non-recommendation (`context.rank`)
   path.

5. **Docs** — `docs/FRONTEND_FEED_API.md`: document `offset` (int ≥ 0, default 0),
   the cross-page stability guarantee, global `rank`, the empty-page end signal,
   and the `FEED_SNAPSHOT_SIZE` max depth. OpenAPI reflects the new query param.

`src/lib/recommendation-client.ts` needs **no signature change** — it still sends a
numeric `limit`, now the snapshot size.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- **Stability:** page 1 (`offset=0`) and page 2 (`offset=limit`) come from one
  cached snapshot — no overlap, no reordering, `cacheStatus: "hit"` on page 2,
  contiguous global `rank`.
- **Paging past the end** returns `places: []` (`200`); a partial final page has
  `length < limit`.
- **Personalized + every `*_fallback` path** honor `offset` (anonymous, no-signals,
  empty-recommendation, rec-service-down).
- **Cache key** ignores `limit`/`offset`: two different `limit`s for the same user
  reuse the same snapshot (no extra rec call).
- Migration applies on a fresh DB; feeds shallower than 50 are byte-for-byte
  unchanged; OpenAPI shows `offset` on the feed query.

## Dependencies

- **Upstream:** none.
- **Downstream:** none.

## Out Of Scope

Cursor/`feedId` tokens; moving the feed cache to Redis (tracked under
`TBD_PLATFORM_HARDENING` concern 7); changing the recommendation contract; the
photo-list (`TASKS_36`) and `googlePlaceId` (item 3, dropped) requests.
