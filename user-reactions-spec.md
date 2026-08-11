# User reactions (favorite / dislike / hide) — backend spec

Spec for end-to-end support of explicit user reactions in `backend_sloco`
(gateway + recommendation service). Self-contained: all upstream facts needed are
restated here; no access to the SLOCO research repo required.

## Problem & goal

The backend supports exactly one user↔place signal today: **saved places**
(global saves + collections; the default collection doubles as "Want to go"). The
recommender's source-of-truth system (SLOCO research repo) additionally defines
**reaction semantics** — favorite / dislike / hide — that never reach this backend:

- There are no reaction endpoints and no DB storage for reactions
  (`services/gateway/supabase/migrations/` has no such table; checked through `014_*`).
- The feed request to the recommendation service carries only two lists
  (`favourites_place_ids`, `want_to_go_place_ids` — built in
  `services/gateway/src/modules/feed/stores/feed.store.ts:57-85`).
- The recommendation service (`services/recommendation`) accepts nothing else either
  (`src/recommendation_service/recommendations/schemas.py:4-12`).

Consequence: a user cannot say "not this" — a disliked or hidden place can be
recommended again forever.

**Goal ("done"):** a user can set exactly one reaction (favorite | dislike | hide) on a
place; reactions are stored; favorites strengthen personalization seeds; disliked and
hidden places are **never returned by any feed path** (personalized or fallback); the
recommendation-service contract carries the new lists.

## Source-of-truth semantics (context)

Upstream (SLOCO `collaborative_sandbox.py` + dashboard) defines:

- Event vocabulary: positive `favorite` (weight 1.2), `like` (1.0), `maps_click` (0.4);
  negative `dislike` (1.0), `hide` (1.2); plus `impression` log.
- Pipeline: content scoring → **hard-exclude the user's disliked + hidden places** →
  optional collaborative re-rank → optional hide-repulsion.
- Semantic distinction (upstream comment): a `dislike` hard-excludes **that exact
  place** (no generalization); `hide` means "show me **less like this**" (excludes and,
  in a later stage, repels similar places).

This spec implements the storage + hard-exclusion + favorite-seeding layer only. The
"repel similar" behavior (hide-repulsion) and collaborative engines are explicitly
out of scope (see Follow-ups) — but dislike and hide MUST be stored as distinct values
from day one so the follow-up can differentiate them.

## Design decisions (settled — raise before implementing if you disagree)

1. **One mutually exclusive reaction per (user, place):** `favorite | dislike | hide`.
   Setting a new reaction replaces the old one. (Upstream keeps overlapping sets and
   also has `like`; the product model is deliberately simpler. `like` is reserved for
   the future — do not implement it.)
2. **Reactions are independent from saves.** Disliking a saved place does not unsave
   it; exclusion wins at recommendation time (engine-side, see B2).
3. **Favorites strengthen seeds:** explicit favorites join `favourites_place_ids`
   (weight 1.0 upstream) in addition to the current saved-derived list. No third seed
   tier — the engine only supports two.
4. **Exclusion is enforced in the engine** (single choke point, covers any future
   direct API caller), **and** in the gateway for feed paths that bypass the
   recommendation service (fallbacks).

## Scope

### Workstream A — gateway (`services/gateway`)

**A1. Migration `015_create_place_reactions.sql`:**
table `public.place_reactions`:
`user_id uuid` (FK `auth.users`, cascade), `place_id bigint` (FK `public.places`,
cascade), `reaction text` CHECK in (`'favorite','dislike','hide'`), `created_at`,
`updated_at timestamptz`. PK `(user_id, place_id)`; index `(user_id, reaction)`.
RLS enabled, service-role access — copy the pattern of
`005_create_saved_places.sql`.

**A2. Endpoints** (auth required, same guard/style as saved-places controllers,
`src/modules/saved-places/` as the reference; new module `src/modules/reactions/`):
- `PUT /v1/me/places/:placeId/reaction` body `{ "reaction": "favorite" | "dislike" | "hide" }`
  → upsert; 404 if place doesn't exist.
- `DELETE /v1/me/places/:placeId/reaction` → remove row (idempotent 204).
- `GET /v1/me/reactions` → `{ "favorites": number[], "dislikes": number[], "hidden": number[] }`
  (internal numeric place ids, same id space as saved-places endpoints).

**A3. Signal derivation** (`feed.store.ts` `getSavedSignals`, lines 57-85): extend to
return four lists (rename to `getUserSignals`):
- `favouritesPlaceIds` = explicit favorites ∪ current derivation (saved not in
  want-to-go, incl. its existing "all saved" fallback at lines 80-82); explicit
  favorites first, deduped.
- `wantToGoPlaceIds` = unchanged.
- `dislikePlaceIds`, `hidePlaceIds` = from `place_reactions`.
All four mapped to `places.source_id` exactly like today (drop rows without
`source_id`, dedupe preserving order — reuse the existing helper, lines 204-218).

**A4. Feed service** (`src/modules/feed/services/feed.service.ts`):
- Request body to the recommendation service (built at lines 133-140, type in
  `src/lib/recommendation-client.ts:10-17`) gains `dislike_place_ids: string[]` and
  `hide_place_ids: string[]`.
- **Every fallback path** that returns places without calling the recommendation
  service (all `*_fallback` personalization statuses) must filter out the requesting
  user's disliked + hidden places. Where the request has no authenticated user the
  filter is a no-op — anonymous behavior is unchanged.
- Personalization trigger: unchanged (needs seeds). A user with only dislikes/hides
  and no saves/favorites still short-circuits to `no_signals_fallback` — but filtered.
- Cache key (`createRecommendationCacheKey`, lines 303-319): include the dislike and
  hide lists (and the now-extended favourites) in `signalHash`, so a new reaction
  takes effect on the next feed call.

**A5. Read-side echo:** add `reaction: 'favorite' | 'dislike' | 'hide' | null` to the
feed card schema (`src/modules/feed/common/feed.schemas.ts:64-87`) and the place
detail response (`src/modules/places/…schemas.ts`, next to the existing
`isSaved`/`savedCollectionIds`). Map pins and search results: unchanged.

### Workstream B — recommendation service (`services/recommendation`)

**B1. Contract** (`src/recommendation_service/recommendations/schemas.py`):
- `PersonalizedRequest` += `dislike_place_ids: list[str]` and
  `hide_place_ids: list[str]` (both `default_factory=list`).
- `InputSummary` += `dislike_count: int`, `hide_count: int` (sizes of the deduped
  incoming lists). All other response fields unchanged (additive change only).

**B2. Engine** (vendored
`src/recommendation_service/algorithms/location_recommender/backend_recommender.py`,
`recommend()` signature at ~line 1054): add keyword-only parameter
`exclude_place_ids: Sequence[str] | None = None`:
- Remove these ids from BOTH seed lists (favourites/want_to_go) before profile
  building, and from the candidate pool **before scoring** (so `limit` is unaffected)
  — analogous to the existing `exclude_input_places` handling.
- The cold-start/fallback path (quality-ranked, triggered below
  `min_saved_for_personalization`) must apply the same exclusion.
- Mark the addition with a short comment `# backend extension (not in upstream)` —
  this file is vendored research code; keep the diff minimal and greppable.

**B3. Adapter + service**
(`algorithms/location_recommender/adapter.py:49-61`,
`recommendations/service.py:24-30`): pass the two lists through;
`exclude_place_ids = dedup(dislike + hide)`; fill the two new `InputSummary` counts.
The legacy `embedding_recommender_v1` algorithm (`algorithms/embedding_recommender.py`)
must ACCEPT the new request fields without crashing; implementing exclusion there is
optional — if skipped, say so explicitly when handing the work back.

### Workstream C — contract docs

- `services/gateway/docs/FRONTEND_FEED_API.md`: document the `reaction` card field and
  the three reaction endpoints.
- `services/recommendation/README.md` (or its docs/): document the two new request
  fields and `InputSummary` counts.
- OpenAPI (both services generate from schemas — verify the new fields appear).

## Out of scope — do NOT implement

- `like` reaction, impression/`maps_click` event log, any event-ingestion pipeline.
- Hide-repulsion ("less like this" similarity penalty), collaborative engines
  (MF/BPR/user-kNN), popularity/taste modes, taste-match re-rank.
- Onboarding artifacts, `/similar` (item-to-item) endpoint.
- Image modalities / weight-preset changes / `RECOMMENDER_ALGORITHM` default flip.
- Re-syncing the vendored `backend_recommender.py` with upstream (known drift:
  upstream has `cross_theme_inject_window` + `interleave_into_window()`; irrelevant
  while cross-theme is unreachable here).
- Any frontend work; any change to saved-places behavior.

## Behavior details & edge cases

- Upsert replaces: favorite → dislike on the same place = one row with `dislike`.
- A place id present in BOTH a seed list and `exclude_place_ids`: excluded (removed
  from seeds; never in results). It must NOT be reported in `invalid_place_ids`.
- Ids in `dislike_place_ids`/`hide_place_ids` unknown to the catalog: silently
  ignored (counts still reflect the received deduped list sizes).
- `GET /v1/me/reactions` for a user with none: three empty arrays, 200.
- Reaction on a place the user has never saved: fully supported (that's the point).
- Determinism and "no network calls at request time" in the recommendation service
  are preserved.

## Acceptance criteria

1. Migration applies cleanly on a fresh DB; existing migrations untouched.
2. Gateway tests (repo's standard runner): PUT/DELETE/GET reaction round-trip;
   mutual exclusivity (second PUT replaces); 404 on unknown place; auth required.
3. Recommendation-service pytest: a request whose `dislike_place_ids` contains a
   catalog place returns results without it; same for `hide_place_ids`; same when the
   excluded id is also in `favourites_place_ids` (and it's absent from
   `invalid_place_ids`); exclusion also holds on the cold-start path (0–2 seeds);
   `dislike_count`/`hide_count` correct; response shape otherwise unchanged
   (existing tests green, incl. `tests/test_location_recommender_v4.py`).
4. Feed integration (gateway tests with mocked rec-service + fallback paths): a
   disliked/hidden place appears in NO feed response variant; after a new reaction
   the next feed call reflects it (cache key changed).
5. Feed card `reaction` echo present; OpenAPI of both services shows the new fields.
6. Both services' full existing suites pass.

## Implementer's latitude

- Your call: internal naming, file layout inside the new `reactions` module, index
  details beyond those listed, test organization, exact SQL trigger vs app-side
  `updated_at` maintenance.
- Fixed (don't change without raising it first): existing contract field names/types,
  no new dependencies, engine defaults/weights/config values untouched, changes
  limited to `services/gateway`, `services/recommendation`, and the docs named above.

## Follow-ups (recorded, NOT part of this task)

- Hide-repulsion port (upstream `apply_hide_repulsion`, Rocchio, β/cap 0.30) so `hide`
  generalizes; requires the reaction storage from this task.
- Behavioral event log (`impression`, `maps_click`) for the future collaborative layer.
- Re-sync vendored `backend_recommender.py` with upstream before exposing cross-theme
  injection (restores `cross_theme_inject_window` fix).
- Expose `user_lat`/`user_lon` + a `geo_distance` weight (currently unreachable).
- Surface `reason_tags`/`profile_id` to improve `whyRecommended` (adapter drops them).
- Prod config hygiene: ensure `RECOMMENDER_ALGORITHM=location_recommender_v4` is set
  (config default is still `embedding_recommender_v1`).
