# iOS asks — backend implementation plan

**Date:** 2026-08-11 · **Sources:** the seven open spec files in
`frontend_new/messages-to-backend-dev/not-done/` (plus the two in `done/`), compared line-by-line
against this repo's code (gateway `src/`, `supabase/migrations/`, `scripts/`, and
`services/recommendation/`).

This is the working plan. Each section states: what the code actually shows (with file
references), the verdict for the spec, and the concrete steps. Suggested execution order is at
the end.

## Scoreboard

| # | iOS ask (spec file) | Verdict from code | Size |
|---|---|---|---|
| — | `FEED_OFFSET_PAGING` (done/) | ✅ shipped — migration `016`, verified live | — |
| — | `PLACE_PHOTOS_LIST` (done/) | ✅ shipped — `TASKS_36`, commit `0c6e9ed` | — |
| 1 | `RECOMMENDER_STATUS` | Personalized path fully built in the gateway. **Update 2026-08-11: v4 is live and runtime-verified** — repo Variable set 2026-07-12; server log `algorithm=location_recommender_v4_more_direct candidates=12578` (full catalog, P0-2 trap did not fire). Remaining: authenticated end-to-end check + guard deploy (rec `TASKS_6`) | S (verify) |
| 2 | `ONBOARDING_STATUS_WRITE` | Confirmed GET-only; **a ready plan already exists — `TASKS_38` (`POST /v1/onboarding/complete`), planned, never implemented** | M |
| 3 | `PLACE_DETAILS_MISSING_FIELDS` | **Not a serializer drop — the source catalog never had the fields.** Except `price_level`, which IS in the source CSV (2 959/12 578 rows) yet null in prod → import discrepancy | S+L |
| 4 | `FEED_FILTERS_AND_DEPTH` | Confirmed: no `category`, snapshot = hard `FEED_SNAPSHOT_SIZE = 100`; RPCs and rec-service already allow 200 | S–M |
| 5 | `FEED_SORT_SPEC` | Confirmed: no `sort`; but `distance_m` is already computed by both feed RPCs → pure app-layer change, **no migration** | S |
| 6 | `SEARCH_CATEGORY_FILTER` | Confirmed: trigram text match only, `lat/lng` is a rank boost (max 10 pts vs 100 for text) → RPC change needed | M |
| 7 | `MAP_TILE_DENSITY` | Confirmed: global per-zoom **score floor** (the instrument the spec rejects), no per-tile cap. One RPC call = one tile → the cap is literally a `LIMIT` | S |

---

## 1. Recommender status (`RECOMMENDER_STATUS.md`)

### What the code shows

The personalized path is fully implemented in
`services/gateway/src/modules/feed/services/feed.service.ts`:

- **Entry condition** (`hasSignals`, line ~335 + `FeedStore.getUserSignals`): authenticated AND
  (favourites ∪ want-to-go) non-empty. *Favourites* = explicit `favorite` reactions ∪ saved
  places outside the "want to go" collection (any saved place counts if no explicit ones exist).
  **One favourite reaction or one saved place is enough. `dislike`/`hide` do NOT enter the
  personalized path — they only exclude places** (`excludedSourceIdsFromSignals`).
- With signals, the gateway calls the rec-service
  (`POST /v1/recommendations/personalized`, `src/lib/recommendation-client.ts`) with
  `limit = 100`, caches the response in-process 10 min keyed by `userId + signals hash`.
- **The rec-service is deployed**: `docker-compose.yml` runs `recommendation-service:8000` and
  the gateway defaults `RECOMMENDATION_SERVICE_URL` to it. BUT the compose default is
  `RECOMMENDER_ALGORITHM: embedding_recommender_v1` (the legacy cosine ranker). The v4 port
  (`location_recommender_v4`, reports `algorithm_version: "location_recommender_v4_more_direct"`)
  is **dormant unless the server `.env` overrides it** — this is exactly
  `recommender-config-audit.md` P0-1, still unresolved.
- **`matchScore` on every fallback** = `round(clamp(map_visibility_score, 0, 100))`
  (`feed.mappers.ts` `getMatchScore`/`normalizeScore`) — the iOS measurement
  (`matchScore == round(mapVisibilityScore)` on 97/100) is confirmed by code, not coincidence.
- **`whyRecommended`**: fixed sentence per fallback status; on the personalized path it is
  `ai_the_move ?? "Because this matches places you saved."` — i.e. it DOES vary per card once
  personalization is live.
- **`debug=true`**: gateway bypasses its cache (`cacheStatus: "bypass"`) and forwards
  `debug` to the rec-service, which then fills per-item `similarity`
  (`recommendation/src/.../recommendations/service.py:39`) — **but the gateway drops it**
  (`feed.service.ts` maps only `rank/place_id/score`). So from the app, `debug` currently only
  bypasses the cache.

### Answers to the spec's numbered questions (to send to iOS)

1. Service deployed & reachable; version depends on server `.env` — flip to v4 (below). Once
   flipped, `algorithmVersion` reads `location_recommender_v4_more_direct`.
2. One `favorite` reaction OR one saved place flips the user to `personalized`. Dislike/hide
   don't count as entry signals.
3. `onboardingStatus` values: `not_started` (DB default, migration `004`) → writer lands with
   `TASKS_38` adding `completed | skipped`. See §2.
4. Fallback `matchScore`/`whyRecommended` are **cosmetic** — recommendation: keep the wire shape
   (nulling would break the iOS decoder) and let the client key the badge/pill off
   `personalizationStatus != "personalized"`, which it already receives.
5. `debug=true` today = cache bypass only; optionally we forward `similarity` +
   `input_summary.candidate_count` in debug responses (small gateway change).
6. Verification: seed one favourite on a test account, then one authenticated feed request.

### Steps

1. ~~Server `.env` flip~~ — **already done (found 2026-08-11):** `deploy-production.yml`
   renders `/opt/backend_sloco/.env` on every deploy and appends the full v4 block (all
   vars pinned together, exactly the audit's P0-2 shape) when the GitHub repo Variable
   `RECOMMENDER_ALGORITHM=location_recommender_v4` is set — which it has been since
   2026-07-12, with green deploys after. Never hand-edit the server `.env`. Runtime check:
   `docker compose logs recommendation-service | grep "Loaded recommender"` → expect
   `algorithm=location_recommender_v4_more_direct candidates=12578`.
2. Add the audit's cheap guard: at startup log embedding coverage vs locations rows, WARN < 95%.
3. (Optional, S) Forward `similarity` and `candidate_count` when `debug=true`.
4. Verify per the spec's own check: authenticated request with ≥1 favourite →
   `personalizationStatus == "personalized"`, `algorithmVersion == "location_recommender_v4_more_direct"`,
   `embeddingRunId == "combined_food_ttd"`, `whyRecommended` varies. Paste the meta into
   `RECOMMENDER_STATUS.md` and answer Q1–Q6 there.

---

## 2. Onboarding status write (`ONBOARDING_STATUS_WRITE.md`)

### What the code shows

- `/v1/me` is GET-only, confirmed: `me.controller.ts` registers exactly two GETs (`/v1/me`,
  `/v1/me/saved/ids`). `MeStore.upsertDefaultProfile` inserts only `user_id`; **nothing ever
  writes `onboarding_status`** (column: bare `text`, default `not_started`, migration `004`).
- **A ready, approved-shape plan already exists**: `docs/tasks/TASKS_38_ONBOARDING_COMPLETE.md`
  ("Planned, awaiting approval") — new `onboarding` module +
  `POST /v1/onboarding/complete` with body `{ pickedPlaceIds: number[], status: "completed" | "skipped" }`:
  picks → `saved_places` (which the feed already counts as favourites → instant entry into the
  personalized path, no cache bust needed), then `update profiles set onboarding_status`.
  This satisfies the iOS ask (one value, set once, readable on any device via `/v1/me`) and
  simultaneously closes the frontend backlog item "interests endpoint / move OnboardingProgress
  server-side" — the deck's liked places ARE the interests input.

### Steps

1. Implement `TASKS_38` as written (module scaffold `src/modules/onboarding/`, endpoint, tests).
2. Document the vocabulary in OpenAPI: `MeProfile.onboardingStatus` →
   `z.enum(["not_started", "completed", "skipped"])` in `me.schemas.ts` (values are fully
   controlled: DB default + this single writer).
3. Reply to iOS: endpoint shape differs from their `PATCH /v1/me` suggestion (they explicitly
   allowed any shape); they branch on `== "completed"`, delete the
   `AuthService.isNewlyCreatedAccount()` stopgap and the device-local `OnboardingProgress` key.
4. `TASKS_39` (onboarding tree) / `TASKS_40` (similar) stay separate — not blocking this ask.

**Acceptance:** `POST /v1/onboarding/complete` with 3 picks → `GET /v1/me` returns `completed`;
next `GET /v1/feed/places` (same user) is `personalized`. 401 without token. OpenAPI shows the
enum.

---

## 3. Place details empty fields (`PLACE_DETAILS_MISSING_FIELDS.md`)

### What the code shows — the spec's central question answered

The spec asked: *"serializer drop, or never ingested?"* — **never ingested, and (except
`price_level`) never present in the source data at all.**

- DB columns exist and the read path is clean end-to-end: migration `009_places_v2.sql` adds
  `formatted_address`, `short_formatted_address`, `business_status`, `google_maps_uri`, `phone`,
  `website_url`, `opening_hours jsonb`, `price_level`; RPC `place_details_by_id` selects them;
  `places.mappers.ts` maps them 1:1. Nothing drops anything.
- The primary import (`scripts/integrations/sloco/map.ts`, source `sloco_ai`) has a fixed
  `SLOCO_COLUMNS` list that **does not include any of these fields** — and the source catalog
  itself (`services/recommendation/artifacts/locations_combined_food_ttd.csv`, 12 578 rows,
  60 columns) **has no address / hours / phone / website / business-status / maps-uri columns.**
  The data team's enrichment never carried them.
- Measured in the source CSV: `price_level` **2 959/12 578 non-empty (23.5%)** — yet prod serves
  null on 0/30 sampled + 400/400 map pins. With a 23.5% base rate, 0/30 is ~0.03% likely by
  chance ⇒ the prod table was imported before `price_level` was mapped (or the operator import
  skipped the column). `price_min_ron`, `serves`, `features`: **0/12 578** in source — genuinely
  empty, which answers the "six always-`{}` objects" question: reserved, not lost.
- `google_maps_uri` needs no external data at all: `source_id` for `sloco_ai` **is the numeric
  Google CID**, and `https://maps.google.com/?cid=<source_id>` is the canonical place-card link.

### Steps (split by dependency)

**Quick wins (no external data):**
1. `price_level` backfill — one operator SQL `UPDATE public.places SET price_level = ...` joined
   from the repo CSV by `source_id` (script or temp table; the CSV is already in-repo). Verify
   live: sampled coverage roughly matches 23.5%, concentrated where the source has it.
2. `google_maps_uri` backfill — `UPDATE public.places SET google_maps_uri =
   'https://maps.google.com/?cid=' || source_id WHERE source = 'sloco_ai' AND google_maps_uri IS
   NULL;` + add both columns to the sloco mapper so future imports carry them.
3. Reply to iOS on `serves`/`features`/`googleDetails`/etc.: reserved/empty at the source —
   stop treating as missing.

**The long pole (external data — start the conversation now, in parallel with everything else):**
4. `openingHours`, `formattedAddress`/`shortFormattedAddress`, `businessStatus`, `phone`,
   `websiteUrl` require a **new enrichment pass keyed by CID** — either the data team re-exports
   the catalog with these Google Places fields, or we run a one-off backend fetch (Google Places
   Details; needs an API key + budget sign-off; 12.5k places is a bounded one-time cost).
   Deliver as an UPDATE-style import (extend `sloco/map.ts` + a keyed update, NOT the
   truncate-and-reimport flow). Priority order from the spec: hours → address → (price ✓) →
   maps-uri (✓ via CID) → business_status → phone/website.

**Acceptance:** the spec's own `curl /v1/places/12474` check returns non-null
`openingHours/shortFormattedAddress/priceLevel/googleMapsUri` for places that have them in
Google; iOS then deletes the placeholder address/hours in `PlaceCardView` (their tracked task).

---

## 4 + 5. Feed: `sort`, `category`, depth (`FEED_SORT_SPEC.md`, `FEED_FILTERS_AND_DEPTH.md`)

### What the code shows

- `feed.schemas.ts`: query = `limit ≤ 50, offset, lat, lng, city, country, debug` — no `sort`,
  no `category`. Confirmed.
- Depth: `FEED_SNAPSHOT_SIZE = 100` (`feed.service.ts:28`) is the only reason `offset=100 → []`.
  Migration `016` already raised both feed RPC caps to **200**, and the rec-service caps at
  `RECOMMEND_MAX_LIMIT` (default **200**, env up to 1000) — so a 200-deep snapshot is a
  one-line gateway change.
- Both feed RPCs already return `distance_m` (computed from `lat/lng`) for every row → *sort by
  distance is a pure gateway re-order of the snapshot — exactly the "same candidates, different
  order" semantics the spec demands, and no migration.*
- Fallback ordering (`feed_fallback_places`, migration `016`): visibility + 0.20·rating +
  0.15·popularity + city/country/proximity boosts over the whole table.
- Cache: only the rec-service *response* is cached (keyed by user+signals). Sort/filter applied
  after the cache → **no cache-key change needed** for either parameter.

### Steps — `sort` (do first, it's the cheapest spec)

1. Schema: `sort: z.enum(["relevance", "distance"]).default("relevance")` + refine
   `sort=distance requires lat/lng` → both invalid cases become the existing 400
   `ValidationErrorResponse` (spec's validation table satisfied).
2. Service: build the snapshot as today; when `sort=distance`, stable-sort by `distance_m` asc
   **before** the offset slice (JS sort is stable → ties keep relevance order, spec §ties), and
   assign `rank = index + 1` over the sorted snapshot (spec wants positional rank; note the
   personalized path currently uses the recommender's rank — switch to positional only under
   `sort=distance`). Slice `offset..offset+limit` after sorting → windows continue the distance
   ordering (spec AC-4).
3. Echo `feed.sort` in the meta (spec marks it optional; it's one field and makes AC testable).
4. Tests: the spec's 7 acceptance criteria verbatim (`feed.routes.test.ts` /
   `feed.service.test.ts`).

### Steps — depth

5. `FEED_SNAPSHOT_SIZE` 100 → 200. Answer the spec's question: "the ranked snapshot now goes
   200 deep; deeper needs a raise of `RECOMMEND_MAX_LIMIT` + RPC caps (a legitimate migration)
   when the product needs it."

### Steps — `category` filter

6. Define the coarse bucket vocabulary **in the gateway** (one shared const):
   `cafe, food, bar, culture, nature, shopping, leisure` → keyword lists matched against
   `category`/`primary_type`/`types` (mirror the client's `MapChipFilter` buckets; send iOS the
   final vocabulary — their spec explicitly offers to map chips onto ours).
7. Fallback path: add `category_keywords text[] default null` to `feed_fallback_places`
   (CREATE OR REPLACE, migration `017`) — WHERE-match **before** scoring, so a filtered feed is
   still a full snapshot (the spec's core complaint).
8. Personalized path (MVP): request the rec-service at a higher limit (200) and bucket-filter the
   hydrated rows in the gateway; document that a filtered personalized feed can be shallower
   than 100. Teaching the rec engine categories is a later, separate task.
9. Schema: `category` as repeatable/CSV per spec; unknown value → 400.
10. Small related note from the spec: the hide sheet collects a `reason` the API drops — add
    optional `reason` to the reaction PUT body + a nullable `reason` column on
    `place_reactions` (tiny migration) so the signal is stored for the recommender. (P3.)

**Acceptance:** spec AC 1–7 for sort; `category=bars&limit=50` returns 50 bar-bucket places
(not "the 3 that survived"); `offset=100` returns places 101–200.

---

## 6. Search category + radius (`SEARCH_CATEGORY_FILTER.md`)

### What the code shows

`search_places` (migration `011`): trigram `word_similarity` over name + `search_keywords`
(name/category/primary_type/types/ai_tags). Rank = `100·text_match + 30·exact + 15·prefix +
12·same_city + 6·same_country + 10·nearby + 8·quality + 5·popularity` — the name term is an
order of magnitude above the proximity boost (`10/(1+km)`), which is exactly why a Tbilisi
"…Cafe…" name-match outranks a Bucharest café 1 545 km closer. There is **no radius constraint
and no category parameter**. Confirmed.

### Steps

1. Migration `018` — extend `search_places` (CREATE OR REPLACE, non-destructive):
   - new params `category_keywords text[] default null`, `radius_meters integer default null`;
   - `q` becomes optional when `category_keywords` is present (**browse mode**): WHERE
     bucket-match (+ `st_dwithin(geom::geography, origin, radius_meters)` when given), ORDER BY
     distance asc (fix present) else `map_visibility_score` desc;
   - text mode (q present) keeps today's ranking, plus the same optional `st_dwithin` WHERE when
     `radius_meters` is given (a hard cut, not a boost — the spec's point).
2. Gateway `search` module: schema `q` optional-with-`category` (zod refine: at least one of
   them), pass-through params, reuse the shared bucket vocabulary from feed §6.
3. Reply to iOS with the vocabulary → they wire the seven chips (tracked on their side as "do
   NOT fake via `q`").

**Acceptance:** `?category=cafe&lat=44.4325&lng=26.1039&radiusMeters=1500&limit=20` returns only
cafés within 1.5 km, nearest-first, zero Tbilisi rows; unknown `category` → 400;
`q`-only requests are byte-identical to today.

---

## 7. Map tile density cap (`MAP_TILE_DENSITY.md`)

### What the code shows

`map_tile(z,x,y)` (migration `014`) filters by a **global per-zoom score floor**
(`map_tile_min_score`: 92/86/76/66/56) — precisely the instrument the spec argues against
(city variance: Tbilisi z13 keeps 235 places over the same floor that serves Bucharest 119).
There is no per-tile cap. Since **each RPC call builds exactly one tile**, the requested
`ROW_NUMBER() OVER (PARTITION BY tile …)` reduces to a plain `LIMIT` on the already-ordered
CTE.

Cache/versioning is ready for the change: Redis keys are `tile:v{MAP_TILE_VERSION}:…`, the ETag
derives from the same env, and the client fetches `?v=` from `/v1/map/config` — bumping
`MAP_TILE_VERSION` busts every layer at once.

### Steps

1. Migration `019` — CREATE OR REPLACE `map_tile` with the spec's per-zoom cap via `LIMIT`:
   `z ≤ 12 → 6`, `z13–15 → 10`, `z16 → 15`, `z17 → 25`, `z ≥ 18 → uncapped` (keep the old floor
   only for the uncapped band as a safety net; below it the cap subsumes it). Ordering stays
   as-is (`map_visibility_score` is zoom-stable — verified by iOS; N is non-decreasing in z ⇒
   zooming in only ever reveals, satisfying the spec's "done means").
2. Bump `MAP_TILE_VERSION` (server `.env` / compose) — do not skip, or Redis + client caches
   serve stale dense tiles for up to 7 days (`MAP_TILE_CACHE_TTL_SECONDS=604800`).
3. Keep `mapVisibilityScore` in the tile payload (already there; the client sorts collision
   priority by it — spec's hard requirement).

**Acceptance (spec's own):** the z13 tile over each city centre carries ~10 features instead of
119/235; the survivors are the top-scored; zoom-in strictly adds.

---

## Suggested execution order — with canonical task files

Each item is now a task doc in the repo's own format (indexed in
`services/gateway/docs/tasks/README.md`; the v4 flip lives with the rec service). This file
stays the cross-service map; **the task files are the implementation source of truth.**

| Step | Item | Task file | Size |
|---|---|---|---|
| 0 | Place-details data conversation (hours/address/phone/site by CID) — start now, longest lead time | `services/gateway/docs/tasks/TBD_PLACE_DETAILS_ENRICHMENT.md` (promotes to `TASKS_NN` once the source decision is made) | L (elapsed) |
| 1 | Recommender v4 `.env` flip + coverage guard + verify (§1) | `services/recommendation/docs/TASKS_6_v4_activation_coverage_guard.md` | S |
| 2 | Map tile cap + version bump (§7) | `services/gateway/docs/tasks/TASKS_41_MAP_TILE_DENSITY_CAP.md` | S |
| 3 | Feed `sort` (§4/5) | `services/gateway/docs/tasks/TASKS_42_FEED_SORT.md` | S |
| 4 | Feed snapshot 100 → 200 (§4) | `services/gateway/docs/tasks/TASKS_43_FEED_SNAPSHOT_DEPTH.md` | S |
| 5 | `price_level` + `google_maps_uri` backfills (§3.1–3.2) | `services/gateway/docs/tasks/TASKS_44_PLACE_DATA_BACKFILLS.md` | S |
| 6 | Onboarding write + `/v1/me` enum (§2) | `services/gateway/docs/tasks/TASKS_38_ONBOARDING_COMPLETE.md` (pre-existing plan + 2026-08-11 addendum) | M |
| 7 | Search `category` + `radiusMeters` (§6) | `services/gateway/docs/tasks/TASKS_45_SEARCH_CATEGORY_RADIUS.md` | M |
| 8 | Feed `category` (§4) | `services/gateway/docs/tasks/TASKS_46_FEED_CATEGORY_FILTER.md` (needs 45's vocabulary) | M |
| 9 | Reaction `reason` column (§4.10) | no task file yet — create `TASKS_NN` when scheduled | S |

Optional, unscheduled (no task file until we decide to do it): `debug=true` passthrough of
`similarity`/`candidate_count` through the gateway (§1.3).

## Ship ritual (the repo's own rules, applied to every item above)

1. Implement per the task file; `pnpm build && pnpm test && pnpm lint` before calling it done
   (`AGENTS.md`).
2. Flip the task's row to **Done** in `docs/tasks/README.md`.
3. Fold lasting behavior into the docs of record: a `DECISIONS.md` row, the relevant
   `docs/FRONTEND_*.md`, and `docs/CURRENT_STATE.md` (task docs are history, not product
   docs).
4. Migrations only when Postgres itself must change; destructive SQL gets the CAPS warning
   (`AGENTS.md`).
5. Close the loop with iOS (the `messages-to-backend-dev/README.md` rule): run the spec's own
   acceptance against production, paste the result into the spec file, flip its status line,
   `git mv` to `done/`, grep for cross-references.
6. Suggest a commit message; Kirill commits and pushes (`AGENTS.md` git rule).

## Housekeeping noticed while auditing (not blocking)

- `docs/tasks/README.md` statuses are stale: `TASKS_32` (vector tiles), `TASKS_34`/`35`
  (reactions), `TASKS_37` (feed pagination) are live in production but still marked *Planned*.
- `recommender-config-audit.md` P1 (re-vendor `backend_recommender.py` after the upstream push)
  remains open and becomes more relevant the moment v4 goes live.
- The gateway drops the rec-service's `similarity` field even in debug — §1.3 makes `debug=true`
  actually useful to iOS.
