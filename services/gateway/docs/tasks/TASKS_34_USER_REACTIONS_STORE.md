# TASKS 34: User Reactions — Storage + CRUD Module

**Status: Planned (awaiting approval).**

Part **2 of 3** of the user-reactions feature. Order: recommendation `TASKS_3`
(contract) → **this (GW storage)** → `TASKS_35` (GW feed integration). This task is
pure gateway and **independent of `TASKS_3`**: it delivers standalone value — a user
can set and read reactions, all persisted — while the feed does not consume them
yet (that is `TASKS_35`).

## Context

The backend has one user↔place signal today: saved places (`src/modules/saved-places/`).
The product needs explicit reactions: one mutually-exclusive
`favorite | dislike | hide` per (user, place). This task adds the table and the
CRUD module only; feed seeding/exclusion and the read-side echo are `TASKS_35`.

## Decisions

- **Reactions are keyed by `places.source_id` (text), not the bigint `places.id`.**
  `places.id` is regenerated on every catalog reimport (`TRUNCATE … RESTART IDENTITY
  CASCADE`, migration 009) — that is exactly why `saved_places` gets wiped on
  reimport. `source_id` is the stable key and is already the key space of
  `place_photos`, the feed signals, and the recommender. So reactions survive
  reimports and the `TASKS_35` signal path needs no joins.
- **The public API stays on the bigint `:placeId`** (parity with saved-places), so
  the frontend does not change its id model. The store translates `id ↔ source_id`.
- **New module, saved-places untouched.** `src/modules/reactions/` is a *separate*
  module built on the same structural template as saved-places — not a replacement
  and not a modification of it.
- **No FK to `places`.** `source_id` is not unique on its own (source-scoped);
  place existence is checked in the app, exactly like `place_photos`.

## Changes (`services/gateway`)

1. **Migration `supabase/migrations/015_create_place_reactions.sql`** (CREATE TABLE
   only — non-destructive):

   ```sql
   create table if not exists public.place_reactions (
     user_id    uuid not null references auth.users(id) on delete cascade,
     source_id  text not null,
     reaction   text not null check (reaction in ('favorite','dislike','hide')),
     created_at timestamptz not null default now(),
     updated_at timestamptz not null default now(),
     primary key (user_id, source_id)
   );
   create index if not exists place_reactions_user_reaction_idx
     on public.place_reactions (user_id, reaction);
   alter table public.place_reactions enable row level security;
   ```
   Service-role access, no policy — same pattern as `005`/`008`.

2. **New module `src/modules/reactions/`** — layered OOP (controller → service →
   store), following the reference in `src/modules/saved-places/AGENTS.md`
   (the 8-step checklist). Auth via `createAuthGuard` (`withUser`). Endpoints:
   - `PUT /v1/me/places/:placeId/reaction` — body `{ "reaction": "favorite"|"dislike"|"hide" }`
     → upsert; `404` if the place does not exist.
   - `DELETE /v1/me/places/:placeId/reaction` → idempotent `204`.
   - `GET /v1/me/reactions` → `{ "favorites": number[], "dislikes": number[], "hidden": number[] }`
     (internal bigint ids — same id space as saved-places endpoints).

   **Store** responsibilities:
   - write path: `id → source_id` lookup (`select source_id from places where id = :placeId`,
     `404` via a `PlaceNotFoundError`), then `upsert onConflict (user_id, source_id)`
     and `delete`;
   - read path: batch `getReactions(userId, ids)` (join `places` on `source_id`,
     return bigint ids grouped by reaction) — analogous to `getSavedPlaceStates`
     in `saved-places.store.ts`. Lives here now; `TASKS_35` reuses it for the echo.

   **Registration:** `src/config/routes.ts` (route enum), `src/app.ts`
   (`AppOptions.reactionsService?` + `registerReactionsModule`), `src/config/swagger.ts`
   (component loop + tag).

3. **Docs** — `docs/FRONTEND_FEED_API.md`: the three reaction endpoints (the feed
   card `reaction` field is documented in `TASKS_35`). Add a `DECISIONS.md` line if
   useful (source_id keying).

## Test Plan

```bash
pnpm build && pnpm test && pnpm lint
```

- `PUT` then `GET` round-trips the reaction; `DELETE` removes it (idempotent `204`).
- Mutual exclusivity: a second `PUT` with a different value replaces the row (one
  row per `(user, place)`, not two).
- `PUT`/`DELETE` on an unknown `:placeId` → `404`.
- All three endpoints require auth (`401` without a token).
- `GET /v1/me/reactions` for a user with none → three empty arrays, `200`.
- Migration applies cleanly on a fresh DB; existing migrations untouched. OpenAPI
  shows the three reaction endpoints.

## Dependencies

- **Upstream:** none. Ships independently (no feed behavior change).
- **Downstream:** `TASKS_35` reads `place_reactions` (signals) and reuses the
  store's batch `getReactions` (echo).

## Out Of Scope

Feed seeding / exclusion / cache-key / echo (all `TASKS_35`); `like`; event log;
any change to saved-places; any frontend work; the bigint-FK migration from the
original spec (replaced by `source_id`).
