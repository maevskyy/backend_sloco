# TASKS 46: Feed — `category` filter on `GET /v1/feed/places`

**Status: In progress** — implemented 2026-08-11 on `dev` (165/165 tests, build/lint/
typecheck clean); remaining: run migration `019` (after `018` — it depends on the norm
columns), deploy, live acceptance. Implementation notes vs the plan below:

- Fallback path filters inside `feed_fallback_places` (migration `019`), before scoring —
  full snapshot depth preserved. Personalized path filters the hydrated rows in the
  gateway with the TS twin of the SQL matcher (`matchesBucketKeywords` on
  `category`/`primary_type`; the SQL side also sees `types`, which the feed row does not
  carry — accepted asymmetry, `primary_type` carries the signal).
- Under a category filter the personalized rank turns positional (the recommender's ranks
  have gaps after filtering); offset windows stay contiguous.
- If the filter empties a personalized snapshot, the service falls into the existing
  `empty_recommendation_fallback` → the fallback RPC applies the same filter — consistent
  category behavior on both paths.
- **Backward compatible with the currently deployed gateway**, same reasoning as `TASKS_45`:
  `category_keywords` defaults to NULL, so the old 5-named-argument call resolves and
  behaves exactly as before.
- **Rollback:** `supabase/rollback/2026-08-11_018_019_rollback.sql` (covers `018` + `019`).

The `category` half of iOS ask
`frontend_new/messages-to-backend-dev/not-done/FEED_FILTERS_AND_DEPTH.md` §2 (the feed's
chips currently filter the already-loaded snapshot on the client, so "bars" can leave three
cards). Depth is `TASKS_43`, sort is `TASKS_42`. Umbrella plan:
`../../../../ios-asks-implementation-plan.md` §4.

## Context (verified in code)

- `feedPlacesQuerySchema` has no `category`. The snapshot is built in `feed.service.ts` from
  either the rec-service (`feed_places_by_source_ids`, order preserved by `input_rank`) or
  `feed_fallback_places` (whole-table scoring), then sliced by `offset/limit`.
- The spec's core requirement: filter **before** ranking/snapshotting, so a filtered feed is
  still a full snapshot — not the handful that survived a client-side cut.
- The rec-service knows nothing about categories (its request is place-id lists + limit).
- Only the rec-service *response* is cached (category-agnostic) — filtering after that cache
  needs **no cache-key change**, same reasoning as `TASKS_42`.

## Decisions

- Vocabulary: the shared seven-bucket table from **`TASKS_45`**
  (`places/common/place-buckets.ts`). Same values on both endpoints, told to iOS once.
- **Fallback path — filter in the RPC** (true "filter before ranking"): add
  `category_keywords text[] default null` to `feed_fallback_places`; WHERE bucket-match
  before scoring/limit, so a filtered fallback feed is still `FEED_SNAPSHOT_SIZE` deep.
- **Personalized path (MVP) — filter in the gateway after hydration**: request the
  rec-service at the full snapshot size, bucket-filter the hydrated rows, then rank/slice. A
  filtered personalized feed can therefore be shallower than the snapshot; documented, and
  the honest answer to iOS ("filtered depth = what survives of the top-200"). Teaching the
  rec engine categories is a later, separate task (rec-service change).
- **Order of operations** (composes with `TASKS_42`): build snapshot → category-filter →
  sort → positional rank → offset slice. Rank is positional over the *filtered* snapshot on
  both paths (the fallback already ranks positionally post-filter today — see
  `excludedSourceIds` handling).
- `category` accepts CSV/repeated values; unknown value → 400 (zod enum).

## Changes

1. **Migration `0NN_feed_fallback_category.sql`** — `CREATE OR REPLACE
   public.feed_fallback_places(..., category_keywords text[] default null)`: bucket-match
   WHERE (same `ilike any` shape as `TASKS_45`) applied in the `scored` CTE before the
   ordering/limit. Non-destructive.
2. `common/feed.schemas.ts` — `category` param (zod enum array via CSV/repeat, optional).
3. `stores/feed.store.ts` — pass `category_keywords` to the fallback RPC.
4. `services/feed.service.ts` — personalized path: post-hydration bucket filter; unified
   filter → sort → rank → slice pipeline for both paths.
5. Docs: `docs/FRONTEND_FEED_API.md` (param + personalized-depth caveat), `DECISIONS.md`
   row, vocabulary + answers into the iOS spec file.

## Test Plan

- `pnpm build && pnpm test && pnpm lint`; service tests: filter-before-slice on both paths,
  rank positional and gapless after filtering, `category` + `sort` + `offset` composition,
  400 on unknown category, no-param requests byte-identical.
- Live: anonymous `?category=bar&limit=50&lat=…&lng=…` returns 50 bar-bucket places (not
  "the 3 that survived"); `?category=bar&offset=50` continues the same filtered snapshot.
- iOS after ship: deletes the client-side chip filter (`FeedStore.visible`) per their
  backlog.

## Dependencies

- **`TASKS_45`** (shared `place-buckets.ts`).
- Composes with `TASKS_42` (sort) and `TASKS_43` (depth); no hard ordering between them.

## Out Of Scope

- Category awareness inside the rec-service (candidate-level filtering) — later task.
- The hide-reason capture (`reason` on the reaction PUT) — small separate item, tracked in
  the umbrella plan §4.10.
- Mood/price chips (no API concept; product/design question on the iOS side).
