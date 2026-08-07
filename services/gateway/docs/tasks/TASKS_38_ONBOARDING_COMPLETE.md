# TASKS 38: Onboarding — `POST /v1/onboarding/complete`

**Status: Planned (awaiting approval).**

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
