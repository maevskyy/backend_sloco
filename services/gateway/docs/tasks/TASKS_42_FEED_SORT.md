# TASKS 42: Feed — `sort=relevance|distance` on `GET /v1/feed/places`

**Status: In progress** — implemented 2026-08-11 on `dev`: `sort` enum + `distance requires
lat/lng` refine in `feed.schemas.ts` (component `FeedSort`, `feed.sort` echo on `FeedMeta`),
`applySort` in `feed.service.ts` (stable re-sort of the whole snapshot before the offset
slice, positional rank, both paths), spec's acceptance criteria as tests (4 new service
tests + 3 new route tests; existing suites updated for the new required query field).
Verified: `pnpm build`, **153/153 tests**, `pnpm lint`, `pnpm typecheck` — all clean (Node
24, the CI version). Remaining: deploy, live smoke (the spec's Bucharest curls), then fold
docs (`FRONTEND_FEED_API.md`, `DECISIONS.md`) and answer/close the iOS spec file.

iOS ask `frontend_new/messages-to-backend-dev/not-done/FEED_SORT_SPEC.md` — a full contract
with validation rules and 7 acceptance criteria; this task implements it verbatim. Umbrella
plan: `../../../../ios-asks-implementation-plan.md` §4–5.

## Context (verified in code)

- `feed.schemas.ts` `feedPlacesQuerySchema` takes `limit, offset, lat, lng, city, country,
  debug` — no `sort`. The client fakes "Nearby" by re-sorting the window it already has.
- **No migration is needed**: both feed RPCs (`feed_places_by_source_ids`,
  `feed_fallback_places`, migration `016`) already return `distance_m` for every row when
  `lat/lng` are passed. Sorting is a pure re-order of the in-memory snapshot in
  `feed.service.ts` — exactly the spec's required semantics ("same candidates, different
  order", §1).
- Cache interplay: only the rec-service *response* is cached (keyed by `userId + signals
  hash`); the snapshot is rebuilt per request. Applying sort after the cache means **no
  cache-key change** — spec's cache criterion (AC-7) is satisfied for free. The fallback path
  is uncached (`not_applicable`).
- Rank today: fallback assigns `rank = index + 1` positionally over the snapshot before the
  offset slice; the personalized path prefers the recommender's own `rank`
  (`feed.mappers.ts` `getRank`).

## Decisions

- `sort` values: `relevance` (default, byte-identical behavior) and `distance`. Unknown value
  → zod rejects → existing 400 `ValidationErrorResponse` (spec: no silent fallback).
- `sort=distance` without `lat`/`lng` → 400 via a schema `.refine` ("sort=distance requires
  lat and lng").
- Under `sort=distance`, **`rank` is positional over the sorted snapshot** (`index + 1`
  before the offset slice) — the spec offers a choice and notes the client already treats
  rank as positional; positional also keeps AC-4 (offset windows continue the ordering)
  trivially true. `relevance` keeps today's rank behavior.
- Ties break by relevance order: sort with a **stable** comparator on `distance_m` asc (JS
  `Array.prototype.sort` is stable) over the relevance-ordered snapshot — spec §ties, and
  determinism for AC-7 toggling.
- Echo the effective sort as `feed.sort` (spec marks it optional; it is one field and makes
  the acceptance criteria assertable in tests).

## Changes

1. `common/feed.schemas.ts` — add `sort: z.enum(["relevance", "distance"]).default("relevance")`
   + the `distance requires lat/lng` refine; add `sort` to `feedMetaSchema`.
2. `services/feed.service.ts` — after the snapshot is built (both personalized and fallback
   paths), when `sort === "distance"`: stable-sort by `distance_m` asc, re-map cards with
   positional rank, then apply the `offset/limit` slice. Thread the effective sort into the
   `feed` meta.
3. `common/feed.types.ts` / `feed.mappers.ts` — small type + rank plumbing.
4. `common/feed.openapi.ts` — the query/response components pick the change up from the
   schemas (generated; no hand-written JSON Schema).
5. Docs: `docs/FRONTEND_FEED_API.md` (+ one `DECISIONS.md` row).

## Test Plan

`pnpm build && pnpm test && pnpm lint`, with `feed.service.test.ts` / `feed.routes.test.ts`
encoding the spec's seven acceptance criteria:

1. no-`sort` ≡ `sort=relevance` (same ids, same order);
2. `sort=distance` → `distanceMeters` non-decreasing, no nulls;
3. same id **set** at `limit=100` for both sorts;
4. `sort=distance&offset=20` continues the distance ordering;
5. `sort=nearest` → 400;
6. `sort=distance` without `lat/lng` → 400;
7. toggling sort back and forth returns the right order each time (no cache pollution).

Live smoke after deploy: the spec's Bucharest curls.

## Dependencies

- None. (Composes with `TASKS_46` — order of operations there: filter → sort → rank →
  slice.)

## Out Of Scope

- The `category` filter and snapshot depth (`TASKS_46`, `TASKS_43`).
- Any change to relevance scoring or new card fields.

## Assumptions

- With `lat/lng` present, `distance_m` is non-null on every row: both RPCs compute it
  unconditionally and `places.latitude/longitude` are `NOT NULL` (migration `002`).
