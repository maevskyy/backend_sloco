# TASKS 53: the default saved collection is "Favorites"

**Status: Done in code — Kirill runs the one-off rename SQL for existing users.**

Kirill's call (2026-08-16): every user gets a default **Favorites** list; it stays hidden
from "My lists" and opens from the Profile → Favorites row.

The mechanism already existed — `saved_collections.is_default`, auto-created by
`ensureDefaultCollection` and excluded from "My lists" by the client. Only its NAME was
wrong ("Want to go"), and the Profile row had no destination.

## Changes

- `DEFAULT_COLLECTION_NAME`: `"Want to go"` → `"Favorites"`
  (`src/modules/saved-places/stores/saved-places.store.ts`). New accounts get the new name.
- Test fixtures + `docs/FRONTEND_MAP_API.md` (the "omitted `collectionIds`" note) follow.
- Nothing else changes: the list is still created on the first saved-dashboard read
  (`getSavedDashboard` → `ensureDefaultCollection`, so opening Profile creates it),
  still `is_default`, still undeletable (`DefaultSavedCollectionDeleteError`).
- iOS: the Profile row opens `ListDetailView` for that collection (see the frontend repo).

## One-off SQL for existing users (Kirill runs it)

Not a migration — no schema changes, just a data rename, guarded so it can only touch
default collections that still carry the old name (idempotent, safe to re-run):

```sql
update public.saved_collections
   set name = 'Favorites'
 where is_default = true
   and name = 'Want to go';
```

## ⚠️ Product decision left open on purpose

The recommender reads the default collection as the WEAK signal: `feed.store.ts`
`isWantToGoCollection()` maps `is_default` → `want_to_go_place_ids` (research weight 0.55),
while `favourites_place_ids` (weight 1.0) is explicit `favorite` reactions plus saved places
that are NOT in the default collection. A plain bookmark tap sends no `collectionIds`, so it
lands in the default list — which is now called "Favorites" in the UI but still weighs 0.55.

This rename does NOT change any weighting (the `is_default` branch is unchanged), so
personalization behaves exactly as before. If "Favorites" should also mean the strong signal,
that is a one-line change in `isWantToGoCollection` + a rec re-check, and it shifts every
existing user's taste profile — deliberately not done here.

## Verification

`pnpm typecheck` / `pnpm lint` clean, saved-places suite 28/28 green, iOS build succeeds.
