# TASKS 39: Onboarding — `GET /v1/onboarding/tree`

**Status: Planned (awaiting approval).**

Part **2 of 5** of the onboarding feature. This serves the **first screen**: the
precomputed per-city tree, post-processed and cached, so the app renders the start
grid with **zero engine calls** on the happy path.

**Depends on `TASKS_4`** (rec-service serves the artifact) and reuses the
`onboarding` module from `TASKS_38`.

## Context

The data team shipped the artifact (handoff `2026-08-01`, checksums verified): two
cities (Bucharest, Tbilisi), 48 roots × 1008 nodes each, `schema_version: 1`. Node
shape (snake_case): `place_id` (= `places.source_id`, string cid), `name`,
`place_type`, `rating`, `rating_count`, `summary`, `photos`
(`["photos_cid/<cid>/NN_vibe.jpg", ...]`, ≤4, best-first), `children` (string cids;
`[]` for leaves). Every `children`/`roots` id is a key in `nodes`; global dedup.

Two post-processing steps the gateway owns (data team's contract + our
verification):

1. **Numeric id.** Nodes are keyed by `source_id`; the UI/API also speak numeric
   `places.id`. Batch-resolve via the **existing** `feed_places_by_source_ids` RPC
   ([migration `012`](../../supabase/migrations/012_feed_places_rpc.sql),
   `FeedStore.feedPlacesBySourceIds` ~`feed.store.ts:119`) — it maps source_id→id
   and preserves order. All 2016 artifact nodes resolve in the prod catalog
   (data team §3, verified).
2. **Photo URLs.** Rewrite each `photos[]` ref → an absolute R2 URL. **R2 is
   live** (verified 2026-08-01): the bucket base is
   `https://pub-7f2171e09b604265a53f21545c05b186.r2.dev` and the files sit under a
   `sloco_ai/` prefix, i.e. `photos_cid/<cid>/X.jpg` → `<base>/sloco_ai/<cid>/X.jpg`
   (both `NN_vibe.jpg` and `NN_all.jpg` variants return 200). So this is a pure
   prefix rewrite + base env var — **no local static serving, no mount, no resize**
   for v1. (`pub-*.r2.dev` is Cloudflare's dev domain, throttled at scale — a custom
   domain in front is a later, separate step.)

## Decisions

- **Fetch the artifact from the rec-service** (`TASKS_4`) via the recommendation
  client, rather than the gateway reading the file directly — keeps the artifact a
  single-owner deliverable and matches the handoff contract.
- **Post-process once per cache fill, not per request.** Cache key
  `onboarding:tree:<city>:<sourceVersion>` via `getCacheStore()`
  (`src/lib/cache/`); the artifact is immutable per `source_version` → cache hard.
- **Response is camelCase** (`sourceId`, `placeId`, `name`, `placeType`, `rating`,
  `ratingCount`, `summary`, `photoUrls`, `children`) + city/sourceVersion/roots
  header — the shape the client expects.
- **Photo base is a new gateway env var** (e.g. `PHOTO_R2_BASE_URL`), not hardcoded.
- **Auth `optionalUser`** — the tree is identical for every new user, anonymous is
  fine.

## Changes (`services/gateway`)

1. **`src/lib/recommendation-client.ts`** — add `onboardingArtifact(city)` (GET
   `/v1/onboarding/artifact?city=`), reusing the private `requestRecommendationService`
   helper + a `measureDependencyMetric` wrapper (mirror `personalizedPlaces`).
2. **`src/config/env.ts`** — add `PHOTO_R2_BASE_URL`
   (`https://pub-...r2.dev`).
3. **`onboarding` module** — `GET /v1/onboarding/tree?city=` (`optionalUser`):
   - service fetches the artifact, resolves `placeId` for all node cids in one
     `feedPlacesBySourceIds` batch, rewrites `photos[]`→`photoUrls[]`
     (`photos_cid/`→`<base>/sloco_ai/`), maps snake→camel, assembles the tree;
   - caches the processed tree per `onboarding:tree:<city>:<sourceVersion>`;
   - `404` for a city the rec-service doesn't have.
   - schemas in `common/onboarding.schemas.ts` (node + tree response); register the
     component in `src/config/swagger.ts`.
4. **Docs** — `docs/FRONTEND_ONBOARDING_API.md`: the tree endpoint, node shape,
   `photoUrls`, cache/immutability semantics.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- `GET /v1/onboarding/tree?city=Bucharest` → 48 roots; every `children` id resolves
  in `nodes`; every node has a numeric `placeId`; `photoUrls` are absolute R2 URLs
  (mocked rec-client + RPC in tests).
- Photo rewrite: `photos_cid/<cid>/00_vibe.jpg` → `<base>/sloco_ai/<cid>/00_vibe.jpg`.
- Second call for the same city is a cache hit (no re-fetch, no re-resolve).
- Unknown city → `404`.
- OpenAPI shows the endpoint + tree component.

## Dependencies

- **Upstream:** `TASKS_4` (rec-service artifact endpoint + committed JSONs).
- **Downstream:** `TASKS_40` reuses the photo-rewrite + id-resolution helpers.

## Out Of Scope

Live/off-artifact expansion (`TASKS_40`); the `complete` endpoint (`TASKS_38`);
local photo serving / resize pipeline / custom R2 domain (R2 dev URL is fine for
v1); the `source_version` drift check (optional, see `TASKS_4`).
