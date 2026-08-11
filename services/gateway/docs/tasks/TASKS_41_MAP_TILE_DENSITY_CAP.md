# TASKS 41: Map — per-tile place cap in `map_tile()`

**Status: DONE** — shipped and verified in production 2026-08-11. `GET /v1/map/config`
reports `tileVersion: 2`; feature counts per centre tile, measured with an MVT parser:
Bucharest z11 209→**6**, z12 74→**6**, z13 119→**10**, z14 55→**10**, z15 36→**10**,
z16 18→**15**, z17 7→**15**, z18 **2** (uncapped); Tbilisi z11 88→**6**, z12 56→**6**,
z13 **235→10**, z14 110→**10**, z15 112→**10**, z16 17→**15**, z17 9→**25**, z18 **1**.
No tile exceeds its cap; z17 Bucharest rising 7→15 is the intended effect of dropping the
score floor below z18 (sparse tiles keep everything). iOS spec closed → `done/`.

Implementation notes below.

**Was: In progress** — implemented 2026-08-11 on `dev`: migration
`017_map_tile_per_tile_cap.sql` (cap via `LIMIT`, floor kept only ≥z18) and
`MAP_TILE_VERSION` 1→2 in `deploy-production.yml`; nginx checklist verified (no
`proxy_cache` on `/v1/*`, the `?v=` bust reaches the gateway). No gateway TS changed, so
the pnpm suite was not required. Remaining (ops, in this order): run migration `017` in
the Supabase SQL editor → deploy (gateway restart picks `MAP_TILE_VERSION=2`) → live
acceptance below → then fold docs (`FRONTEND_MAP_VECTOR_TILES.md`, `DECISIONS.md` row) —
held back deliberately so the docs of record keep describing live behavior.

iOS ask `frontend_new/messages-to-backend-dev/not-done/MAP_TILE_DENSITY.md` (measured live:
one Tbilisi z13 tile carries **235** places, Bucharest z13 **119**; a phone screen is ~1.35
tiles → ~300 places per screenful where Google shows 5–10). Umbrella plan:
`../../../../ios-asks-implementation-plan.md` §7.

## Context (verified in code)

- `public.map_tile(z, x, y)` (migration `014_map_vector_tiles.sql`) filters candidates by a
  **global per-zoom score floor** — `map_tile_min_score(z)`: 92 (≤z10) / 86 (≤z12) / 76 (≤z14)
  / 66 (≤z16) / 56 (else) — and has **no per-tile cap**. A single floor cannot serve both
  cities (the iOS spec's data: the floor that thins Tbilisi empties Bucharest), which is why
  the ask is a cap, not a higher floor.
- Each RPC call builds exactly **one** tile, and the CTE is already ordered by
  `map_visibility_score desc, rating_score_0_100 …, id asc` — so the requested
  `ROW_NUMBER() OVER (PARTITION BY tile)` reduces to a plain **`LIMIT`**.
- Cache/versioning is ready: Redis keys are `tile:v{MAP_TILE_VERSION}:…`
  (`map-tile.service.ts`), the ETag derives from the same env, and the client appends
  `?v={tileVersion}` from `GET /v1/map/config`. Bumping `MAP_TILE_VERSION` busts every layer.
- Two invariants the client depends on (spec's hard requirements, both already hold):
  `mapVisibilityScore` stays in the tile payload (collision priority), and the score is
  zoom-stable.

## Decisions

- **Cap via `LIMIT`, per zoom band** (spec's own table): `z ≤ 12 → 6`, `z13–15 → 10`,
  `z16 → 15`, `z17 → 25`, `z ≥ 18 → no cap`. Postgres treats `LIMIT NULL` as no limit, so one
  `case` expression covers all bands.
- **Drop the score floor for z ≤ 17** (the cap subsumes it; the spec wants sparse tiles to
  keep everything). **Keep the floor for z ≥ 18** (today's 56) so the uncapped band does not
  regress into unbounded clutter. `map_tile_min_score()` stays for that band.
- Monotone-reveal property holds by construction: N is non-decreasing in z, a child tile has
  a subset of its parent's candidates, and the order is score-stable — so zooming in only
  ever adds places (the spec's "done means").
- This is a changed RPC body that genuinely blocks a feature → a legitimate migration per the
  Migration Restraint Rule.

## Changes

1. **Migration `0NN_map_tile_per_tile_cap.sql`** (next free number) — `CREATE OR REPLACE
   FUNCTION public.map_tile(z, x, y)`: same CTE and ordering; `WHERE` keeps the bbox clause,
   applies `map_tile_min_score(z)` only when `z >= 18`; add
   `LIMIT (case when z <= 12 then 6 when z <= 15 then 10 when z = 16 then 15 when z = 17
   then 25 else null end)`. Non-destructive (function body only).
2. **Server env: bump `MAP_TILE_VERSION` 1 → 2** (deploy `.env`; compose default stays `1`
   with env override). Do not skip — `MAP_TILE_CACHE_TTL_SECONDS` defaults to 7 days.
3. Docs: update `docs/FRONTEND_MAP_VECTOR_TILES.md` (tile contents now capped per zoom) and
   add a `DECISIONS.md` row (map density = per-tile top-N, not a global floor).

## Test Plan

- `pnpm build && pnpm test && pnpm lint` (map-tile service tests still pass; add/adjust a
  store test only if one exercises the RPC contract).
- Live acceptance (the iOS spec's own): the z13 tile over each city centre
  (`/v1/map/tiles/13/…?v=2`) contains ~10 features instead of 119/235, and they are the
  top-scored places in that square; a z14 sweep of the same area only ever adds places.
- `curl -sI` the tile: ETag changed with the version bump; `GET /v1/map/config` reports
  `tileVersion: 2`.
- Checklist: confirm `deploy/nginx/backend_sloco.conf` does not cache tiles independently of
  the query string (the `?v=` bust must reach the gateway).

## Dependencies

- None (independent of all other iOS asks).

## Out Of Scope

- Any change to `map_visibility_score` computation or `/v1/map/places` thresholds
  (migration `013`).
- Client-side thinning (explicitly rejected by the iOS side — density is a data decision).
- New icon buckets / marker design (frontend backlog).
