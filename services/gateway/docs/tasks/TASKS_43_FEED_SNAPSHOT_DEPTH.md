# TASKS 43: Feed — raise the ranked snapshot 100 → 200

**Status: DONE** — shipped and verified in production 2026-08-11:
`?limit=50&offset=100` → 50 cards, ranks 101–150; `offset=150` → ranks 151–200;
`offset=200` → `[]`. The depth question is answered in the iOS spec
(`FEED_FILTERS_AND_DEPTH.md`, which stays open for the `category` half only).

**Was: In progress** — implemented 2026-08-11 on `dev`: `FEED_SNAPSHOT_SIZE = 200`
(with a comment pinning why 200 is the ceiling), the four tests asserting the snapshot
limit updated 100→200. Verified: `pnpm build`, 153/153 tests, lint, typecheck — clean.
Remaining: deploy + live smoke (`?limit=50&offset=100` → ranks 101–150; `offset=200` →
`[]`), then answer the depth question in the iOS spec file (`FEED_FILTERS_AND_DEPTH.md`).

Part of iOS ask `frontend_new/messages-to-backend-dev/done/FEED_FILTERS_AND_DEPTH.md`
(§1: "the feed just ends" — `offset=100` returns `[]`; for a swipe-one-place-per-screen feed,
100 is a session, not a supply). Umbrella plan: `../../../../ios-asks-implementation-plan.md`
§4. The `category` half of that spec is `TASKS_46`.

## Context (verified in code)

- The depth is a single gateway constant: `FEED_SNAPSHOT_SIZE = 100`
  (`services/feed.service.ts:28`). It is used for the rec-service `limit`, the hydration
  limit, and the fallback RPC limit — the `offset` slice runs over that snapshot.
- Everything downstream already allows 200 (deliberately, since `TASKS_37`): migration `016`
  caps both feed RPCs at `least(…, 200)`, and the rec-service `RECOMMEND_MAX_LIMIT` defaults
  to 200 (env-raisable to 1000, `recommendation_service/config.py`).
- The recommendation cache key hashes `snapshotSize` (`createRecommendationCacheKey`), so old
  100-deep cached entries miss naturally after the change — no invalidation step.

## Decisions

- Raise to **200 now** (one line; the ceiling everything is already provisioned for). Going
  deeper is a separate decision: it needs `RECOMMEND_MAX_LIMIT` env + a migration raising the
  two RPC caps — do that only when the product actually consumes 200 (the iOS spec asks "how
  deep can it go"; the answer to send: 200 after this task, deeper on demand).

## Changes

1. `services/feed.service.ts` — `FEED_SNAPSHOT_SIZE = 200`.
2. Docs: `docs/FRONTEND_FEED_API.md` (snapshot depth), answer the depth question in the iOS
   spec file.

## Test Plan

- `pnpm build && pnpm test && pnpm lint`; adjust any test pinned to 100.
- Live: `?limit=50&offset=100` returns ranks 101–150; `?offset=200` → `[]`;
  `?limit=3&offset=2` still returns ranks 3–5 (the shipped `TASKS_37` behavior, unchanged).
- Sanity: personalized response time with a 200-row hydration stays acceptable (one RPC, same
  shape).

## Dependencies

- None. Fully independent of `TASKS_42`/`TASKS_46` (but trivially composes).

## Out Of Scope

- Depth beyond 200 (env + RPC-cap migration, on demand).
- `category` filtering (`TASKS_46`).
