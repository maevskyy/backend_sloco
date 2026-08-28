# TASKS 54: three system lists + "save to which list?"

**Status: Done in code — Kirill runs migration `023` and deploys the gateway.**

Kirill's call (2026-08-28): the save button must ASK where to put the place. The picker
is a plain multi-select with a confirm button, and besides the user's own lists it offers
three lists that exist for everybody: **Saved**, **Favorites**, **Been there** — created
automatically, hidden from "My lists", undeletable, pinned to the top of the picker.

Also his explicit rule: **"Saved" must not silently collect everything the user files
elsewhere.** Before this change a save wrote `saved_places` AND the default collection, so
one tap landed in two visible places at once (his original complaint).

## Model

`saved_places` stays the internal union index — it answers "is the bookmark lit?" and
feeds the recommender. The three visible system lists are ordinary COLLECTIONS carrying a
new `slug` (`saved` | `favorites` | `been`); user lists keep `slug = null`. So "Saved" is
now a list you can be out of while still being in Favorites.

## Backend

- **Migration `023_system_saved_lists.sql`** — adds `slug` (+ partial unique index per
  user), labels the current default `favorites`, creates the missing `saved` / `been` rows
  for existing users, moves `is_default` onto `saved`, and orders the three. A user who
  had ALREADY made a list named "Saved" / "Been there" keeps it: the row is adopted
  (slug filled in) instead of colliding with `unique (user_id, name)`. Verified on a real
  Postgres engine, including a second run (no-op) and the rollback script.
- `ensureSystemCollections()` provisions all three on any saved-dashboard read, so a fresh
  account has them before the first save; `ensureDefaultCollection()` now returns `saved`.
- **`PUT /v1/me/saved/places/:placeId/collections`** — the picker's write. Membership
  becomes EXACTLY `collectionIds` (diffed, so untouched lists keep their `created_at` and
  the "recently saved" order). An **empty array unsaves** the place, which is what an
  emptied picker means.
- `DELETE /v1/me/saved/places/:placeId` now also clears list membership — otherwise a place
  stayed in Favorites while its bookmark read "not saved".
- Deleting any list with a slug is refused (was: only the default).
- `slug` is exposed on every collection payload.

## Recommender (deliberate, in the same change)

`feed.store.ts` reads lists as taste signals, so the new lists needed an explicit meaning:

| list | signal | why |
|---|---|---|
| Saved (default) | `want_to_go` (0.55) | unchanged — this is where quick saves land, as before |
| Favorites | `favourites` (1.0) | an explicit pick deserves the strong signal |
| Been there | **none** | a visit is not a preference; a place whose ONLY list is Been there is excluded from both signals |

A place in Been there AND some other list still counts through that other list.

## Client

- `SystemList` (slug → title, picker order, `isSystem` helpers).
- `SaveToListsSheet` — the picker: system lists first, then user lists, live counts,
  pre-ticked with the place's current lists, "New list" inline, one `setCollections` write.
  ⚠️ Stock SwiftUI controls on purpose — no Figma frame for it yet (Kirill: fine for now).
- The map place card's save button opens the picker instead of toggling.
- Profile rows: Favorites and Saved open their list (`ListDetailView`), **Been there opens
  its own screen** (below). "My lists" shows only user-created lists.
- `MyPlacesStore`'s Saved list reads the Saved LIST, not `GET /v1/me/saved/ids` — that
  endpoint returns the union, which is exactly what Kirill did not want to see there.
- `BeenThereView` — Figma `356:3160` (filled) and `356:3523` (empty): visited summary card
  with counted category chips, Recent/All switcher, green check per row, "Pick from N saved
  places" and the tip card. ⚠️ The frame's **Map** segment is drawn but disabled — a map of
  visited places needs the Explore map with a filtered source, which is another feature.
- Quick save (feed rail, onboarding deck) still writes with no list → lands in Saved; a
  sheet mid-swipe would be worse than a default.

## Verification

Backend: `pnpm typecheck` / `lint` clean, **200/200 tests**. Migration + rollback executed
against a real Postgres (pglite) with two users, including the name-collision case that
would otherwise have left a user with NO default list. iOS: build succeeds.

## Kirill's steps

1. Run `supabase/migrations/023_system_saved_lists.sql` in the SQL editor.
2. Commit + push, then deploy the gateway.
3. In the app: save a place → the picker lists Saved / Favorites / Been there + your lists;
   tick one → it appears only there; untick everything → the place is unsaved.
