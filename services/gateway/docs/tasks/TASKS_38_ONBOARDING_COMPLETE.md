# TASKS 38: Onboarding — `POST /v1/onboarding/complete`

**Status: DONE** — shipped and verified in production 2026-08-12.

Anonymous checks: `POST /v1/onboarding/complete` → 401 without a token and with a bad one;
the path, the `Onboarding` tag and all three components are in the OpenAPI document;
`MeProfile.onboardingStatus` publishes `enum: [not_started, completed, skipped]`.

Authenticated end-to-end (real Bearer):

```
POST /v1/onboarding/complete {"pickedPlaceIds":[4096,9139,5032],"status":"completed"}
GET  /v1/me → profile.onboardingStatus == "completed"
GET  /v1/feed/places (same user) → personalizationStatus == "personalized"
                                   algorithmVersion == "location_recommender_v4_more_direct"
```

That last line is the point of the whole task: the picks became favourites and the user
landed on the personalized feed in the same session — which also closed the separate iOS
ask `RECOMMENDER_STATUS`. Both specs are now in `messages-to-backend-dev/done/`.

Built exactly as planned below (177/177 tests, build/lint/typecheck clean; 11 new tests).

Implementation notes vs the plan:

- **The status write is an upsert, not an update.** The profiles row is normally created
  by `GET /v1/me`, but the current iOS build never calls it — a plain `update` would
  silently write to zero rows. `upsert({user_id, onboarding_status}, onConflict user_id)`
  covers both cases; `/v1/me`'s own default-upsert only sends `user_id`, so it can never
  clobber a written status.
- Picks are deduped; saves run before the status write, so a mid-way failure leaves the
  status unset and the whole call safely retryable. `PlaceNotFoundError` per pick is
  skipped (counted out of `savedCount`), any other error propagates as 500.
- The addendum shipped too: `MeProfile.onboardingStatus` is now
  `z.enum(["not_started", "completed", "skipped"])` in the contract (DB stays free text).
- Contract doc: `docs/FRONTEND_ONBOARDING_API.md`. No migration needed.

> **Addendum (2026-08-11):** iOS ask
> `frontend_new/messages-to-backend-dev/not-done/ONBOARDING_STATUS_WRITE.md` lands on this
> task — it needs exactly one writable, enumerated onboarding state readable via `GET /v1/me`
> on any device. Two additions to the scope below:
> (1) document the vocabulary in the contract — `MeProfile.onboardingStatus` becomes
> `z.enum(["not_started", "completed", "skipped"])` in `me.schemas.ts` (safe: `not_started`
> is the only value in the wild — the column has never had a writer; this endpoint stays the
> single one);
> (2) after ship, answer/close the iOS spec file — the client then deletes its
> `isNewlyCreatedAccount()` stopgap and branches on `== "completed"`.
> The DB stays free-text (the Out Of Scope note below is unchanged — the enum lives in the
> API contract, not a CHECK constraint).

Part **1 of 5** of the onboarding feature (data-team handoff `2026-08-01`). This is
the **independent, ship-now** piece: it has **no dependency** on the recommendation
service or the precomputed artifact. It also scaffolds the new gateway `onboarding`
module the other two gateway tasks build on.

Onboarding overview: the app shows a grid of place cards; the user "likes" some;
on finish those picks become the user's initial **favourites** (which already
personalize the feed) and `profiles.onboarding_status` is set. This task persists
that final `complete` call. The first-screen tree (`TASKS_39`) and live expansion
(`TASKS_40`) are separate.

## Context

Everything this endpoint needs already exists (verified):
- `profiles.onboarding_status` — a bare `text` column, default `not_started`
  ([migration `004_create_profiles.sql:4`](../../supabase/migrations/004_create_profiles.sql)),
  **no CHECK constraint**. `GET /v1/me` already returns `onboardingStatus`
  (`me.store.ts`), but **nothing writes it today** — `MeStore.upsertDefaultProfile`
  only inserts `user_id`. This endpoint is the first writer.
- `SavedPlacesStore.savePlace(userId, placeId)` — upsert with
  `onConflict: "user_id,place_id", ignoreDuplicates: true`
  (`saved-places.store.ts` ~261-280), wrapped by `savedPlacesService.savePlace`
  (asserts the place exists, takes a **numeric** placeId). Plain `saved_places`
  rows **not** in the "want to go" collection are counted as **favourites** by the
  feed (`feed.store.ts` `getUserSignals`), which is exactly the strong signal we
  want — so we call `savePlace` and do **not** touch any collection.
- `createAuthGuard().requireUser` (`src/http/auth-guard.ts`) — the auth used by
  `me`/`saved-places`.

Feed-cache busting from the original spec is **dropped** (data-team §4 agreed): a
signal-less user is on the uncached fallback path; after onboarding the changed
signals produce a new cache key → natural miss → personalized immediately.

## Decisions

- **New module `src/modules/onboarding/`** (layered controller→service→store,
  reference `src/modules/saved-places/AGENTS.md`). This task creates the scaffold;
  `TASKS_39`/`TASKS_40` add endpoints to it.
- **Public API uses numeric `placeId`** (parity with saved-places). `savePlace`
  already resolves existence.
- **`onboarding_status` write lives in the onboarding store** (a focused
  `update profiles set onboarding_status = ... where user_id = ...`), not bolted
  onto the me module.
- **No feed-cache bust.**

## Changes (`services/gateway`)

1. **New module `src/modules/onboarding/`** — `index.ts`, `onboarding.module.ts`,
   `controllers/`, `services/`, `stores/`, `common/` (schemas/types/errors),
   `tests/`.
2. **`POST /v1/onboarding/complete`** — `requireUser`. Body
   `{ "pickedPlaceIds": number[], "status": "completed" | "skipped" }` (empty
   array allowed for `skipped`). Actions, in order:
   - for each pick, `savedPlacesService.savePlace(userId, placeId)` (injected;
     idempotent; skips/records ids that fail existence);
   - `store.setOnboardingStatus(userId, status)` — `update profiles`;
   - response `{ "onboardingStatus": status, "savedCount": number }`.
3. **Registration** — route in `src/config/routes.ts`; wire in `src/app.ts`
   (`AppOptions.onboardingService?` + `registerOnboardingModule`), inject
   `savedPlacesService`; component/tag in `src/config/swagger.ts`.
4. **Docs** — a short `docs/FRONTEND_ONBOARDING_API.md` (start it here; the other
   two gateway tasks extend it). Note `onboarding_status` write in `DECISIONS.md`.

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- `complete` with picks → rows in `saved_places`, `onboarding_status = completed`,
  `savedCount` correct; a follow-up `GET /v1/me` shows `completed`.
- `skipped` with `[]` → no saves, status `skipped`.
- Picks are counted as favourites: after `complete`, `GET /v1/feed/places` for that
  user takes the personalized path (has signals).
- Unknown `placeId` in picks is skipped, not a hard 500 (or documented behavior).
- `401` without a token. OpenAPI shows the endpoint.

## Dependencies

- **Upstream:** none.
- **Downstream:** `TASKS_39`/`TASKS_40` reuse this module scaffold.

## Out Of Scope

The tree (`TASKS_39`) and live similar (`TASKS_40`) endpoints; any rec-service
change; feed-cache busting; a CHECK constraint / enum on `onboarding_status`
(kept as free text, matching migration 004); pre-signup client-side pick storage
(client concern).
