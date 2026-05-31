# TASKS 18: Layered Module Migration (Whole App)

**Status: Done.** All phases (0a–0d shared infra, then `map`/`me`/`health`/`auth`)
landed. OpenAPI component ids and paths are unchanged; `pnpm typecheck/test/lint/build`
green. See `docs/CURRENT_STATE.md` and `docs/DECISIONS.md` for the lasting shape.

## Summary

`TASKS_8` decided the target architecture: a **lightweight layered OOP module**
(`controllers/ → services/ → stores/ → common/ → tests/`), with `saved-places`
as the reference implementation and zod as the single source of truth for
request/response schemas + generated OpenAPI.

This task is the **execution plan** to migrate the rest of the backend onto that
standard. Today only `saved-places` follows it. The remaining modules (`map`,
`me`, `health`, `auth`) are still in the old flat Fastify shape, mix HTTP /
business logic / Supabase access in single files, and hand-write OpenAPI JSON
Schema that can drift from their zod validation.

## Why

- One consistent module shape across the app (no "saved-places is special").
- Supabase access isolated in `stores/` so business logic and HTTP stay testable.
- OpenAPI generated from zod everywhere — no second, drifting source of truth.
- Cross-module calls go through `index.ts` contracts only (e.g. `map` enriching
  with saved state).
- New modules (`places`, `recommendations`, `onboarding`, `taste`) inherit a
  proven pattern instead of inventing one.

## Reference

- Architecture decision: `docs/tasks/TASKS_8_REPO_REFACTORING.md`
- Reference module: `src/modules/saved-places/` and its `AGENTS.md`
- Repo layout rules: `docs/architecture/REPO_STRUCTURE.md`

## Scope

In scope: the shared infrastructure (`config/openapi.ts`, `config/http-schemas.ts`,
`src/http/`, a `lib/` DB-error helper), retrofitting `saved-places` onto it, the
module migrations (`map`, `me`, `health`, `auth`), and the wiring files
(`src/app.ts`, `src/config/swagger.ts`). Out of scope: behavior changes, new
endpoints, DB schema changes. **This is a structure + OpenAPI-source refactor; the
public HTTP contract and all OpenAPI component ids stay identical.**

## Target Module Shape (recap)

```text
src/modules/<feature>/
  index.ts                  public entrypoint (contracts, service singleton, OpenAPI components)
  <feature>.module.ts       composition root: build controller, wire defaults, accept test overrides
  controllers/<feature>.controller.ts   Fastify HTTP only
  services/<feature>.service.ts         business logic / orchestration
  stores/<feature>.store.ts             Supabase/DB access (only layer that imports getSupabaseClient)
  common/<feature>.schemas.ts           zod request/response schemas + component registry
  common/<feature>.openapi.ts           generated JSON Schema components + defineRoute helper
  common/<feature>.types.ts             DTO types (z.infer) + store/service contracts
  common/<feature>.errors.ts            domain error classes (if any)
  common/<feature>.mappers.ts           row → DTO mapping (if noisy)
  tests/                                service (fake store) + controller (fake service/auth) tests
```

Dependency rule: `controller → service → store`; inner layers never import outer.

## Shared code taxonomy (by responsibility, not a `shared/` bucket)

Reusable code is split by responsibility. **Do not create a generic `src/shared/`
or `utils/`** — the repo rules forbid a dumping ground, and it always becomes one.

```text
src/lib/        infrastructure adapters only (external world: Supabase client, future clients)
src/config/     app wiring (env, logger, routes, swagger, openapi generator)
src/http/        Fastify HTTP glue, module-agnostic (route wrapper, error→HTTP, auth guard, response log)
src/modules/    feature modules (controllers/services/stores/common)
```

Rule of thumb: "talks to the outside world" → `lib/`; "wires the app" → `config/`;
"framework glue every controller repeats" → `http/`; "domain logic" → a module.
The logger stays in `config/` (it is Fastify config, not a reusable helper).

---

## Phase 0 — Shared infrastructure (do first)

grep shows real cross-module duplication that the per-module migration would
**multiply** (`validatorCompiler` wrapper, `ZodError → 400` + error bodies,
response-summary logging, the zod→OpenAPI generator, `hasPostgresErrorCode`).
Extract it first so modules consume shared helpers instead of copy-pasting.

### Phase 0a — `config/openapi.ts` (zod → OpenAPI generator)

The single highest-value extraction: `saved-places.openapi.ts` is the only place
that generates components today, and Phase 0b + every module (`map`/`me`/`health`)
would otherwise copy it (~×5).

- `buildComponentSchemas(registry)` — encapsulates
  `z.toJSONSchema(registry, { uri: id => id+"#", target: "openapi-3.0", unrepresentable: "any" })`
  plus normalizing each `$id` to the bare id. Returns the component array.
- `makeDefineRoute({ tag, errorResponses })` — factory returning a module-specific
  `defineRoute` (shared `tags`/`security`/error `$ref`s).
- Source the logic verbatim from the existing
  `src/modules/saved-places/common/saved-places.openapi.ts`.

### Phase 0b — `config/http-schemas.ts` (shared HTTP error schemas)

Today the shared error components are scattered and owned by unrelated modules:

| Component `$id` | Currently defined in | Referenced by |
| --- | --- | --- |
| `ErrorResponse` | `modules/health/health.openapi.ts` | health, saved-places (409/500) |
| `ValidationErrorResponse` | `modules/map/map.openapi.ts` | map, saved-places (400) |
| `AuthErrorResponse` | `modules/me/me.openapi.ts` | me, saved-places (401) |
| `NotFoundResponse` | `modules/saved-places` registry | saved-places (404) |

Create one home for cross-module HTTP error schemas, defined as zod and generated
the same way as modules:

```text
src/config/http-schemas.ts        zod schemas + registry + generated `httpErrorComponentSchemas`
```

- Define `errorResponseSchema`, `validationErrorResponseSchema`,
  `authErrorResponseSchema`, `notFoundResponseSchema` in zod, register with the
  **exact same ids** (`ErrorResponse`, `ValidationErrorResponse`,
  `AuthErrorResponse`, `NotFoundResponse`).
- Generate components with `buildComponentSchemas` from Phase 0a.
- Remove `NotFoundResponse` from the `saved-places` registry and the hand-written
  error schemas from `health.openapi.ts` / `map.openapi.ts` / `me.openapi.ts`
  (those modules keep referencing the ids via `$ref`, just stop owning them).
- `src/config/swagger.ts` registers `httpErrorComponentSchemas` once, before
  module components.

> Note: `ValidationErrorResponse` carries a free-form `issues` array (zod
> `error.issues`). Model it as `z.array(z.object({...}).loose())` / `z.unknown()`
> so generation does not over-constrain it.

### Phase 0c — `src/http/` (controller glue)

Module-agnostic Fastify glue every controller repeats. Keep it minimal — do not
force controllers into a rigid base; domain-specific error maps stay in the module.

- `route.ts` — `docsRoute(schema)` → `{ schema, validatorCompiler: () => () => true }`.
- `errors.ts` — shared bodies (`unauthorizedResponse`, …) + `handleCommonError(reply, error)`
  (`ZodError` → 400, fallback → 500). Modules check their domain errors first, then
  delegate to `handleCommonError`.
- `auth-guard.ts` — `createAuthGuard(authService)` producing `withUser` (the
  `saved-places.controller.ts:219` pattern) and `optionalUser` (the
  `map.routes.ts:109` pattern: `null | "invalid" | user`).
- `response-log.ts` — `logResponseSummary(request, { path, ...fields })` (extracted
  from `map` + `saved-places`).
- `extractBearerToken` stays in `auth` (exported via `auth/index.ts`); `http/` works
  through the `AuthService` contract. Decision recorded.

### Phase 0d — Retrofit `saved-places` onto the shared helpers

The reference module must use the new shared infrastructure, or it diverges.

- Rewrite `common/saved-places.openapi.ts` to use `buildComponentSchemas` +
  `makeDefineRoute` (Phase 0a).
- Rewrite the controller to use `src/http/` (`docsRoute`, `handleCommonError`,
  `createAuthGuard`, `logResponseSummary`).
- Move `hasPostgresErrorCode` (`stores/saved-places.store.ts:393`) into
  `src/lib/` (Supabase error helper).
- Tests stay green; `openapi.json` diff stays identical.

### Conventions checklist (apply in every module)

- **Stable ids**: never rename an existing OpenAPI component id — it is the
  published contract. Verify with the before/after `openapi.json` diff (see Test
  Plan).
- **zod → OpenAPI**: generate via the shared helper. Use `target: "openapi-3.0"`
  (the doc is 3.0.3). Use `.min(1)` instead of `.positive()` for positive ints —
  `.positive()` emits a boolean `exclusiveMinimum` that Fastify's serializer
  rejects. Normalize generated `$id` to the bare id (refs stay `Name#`).
- **Validation stays in zod**: controllers `.parse()` request input; keep
  `validatorCompiler: () => () => true` on routes (OpenAPI schema is for docs +
  response serialization, not request validation).
- **DI**: constructor injection; `*.module.ts` is the only place defaults are
  wired; tests pass fakes.
- **Imports**: other modules import from `<feature>/index.js` only.

---

## Phase 1 — `map` (do first after Phase 0)

Frontend-critical and the most mixed module: HTTP, optional auth, zod parse,
ranking, Supabase RPC, and cross-module saved-state enrichment all currently live
across `map.routes.ts` + `map.service.ts`.

### Current → target mapping

| Current | Target |
| --- | --- |
| `map.routes.ts` (route, optional-auth, zod parse, logging, error mapping) | `controllers/map.controller.ts` |
| `enrichSavedState` / `markPlaceAsUnsaved` / `getOptionalAuthenticatedUser` (`map.routes.ts:109-157`) | move into `services/map.service.ts` (auth-helper may stay a small controller/common util) |
| `getMapPlaces` calling `getSupabaseClient().rpc("places_in_bbox")` (`map.service.ts:56`) | `stores/map.store.ts` |
| ranking algorithm (`map.ranking.ts`) | `common/map.ranking.ts` (pure domain, keep as-is) |
| `mapPlacesQuerySchema` (`map.schemas.ts`) | `common/map.schemas.ts` (+ add response zod schemas) |
| hand-written `map.openapi.ts` (`MapPlacesQuery`, `MapPlace`, `MapPlacesResponse`) | `common/map.openapi.ts` generated from zod |
| `mapPlaceRowToPin` mapper | `common/map.mappers.ts` |
| `map.routes.test.ts`, `map.service.test.ts`, `map.ranking.test.ts` | `tests/` |

### Layer responsibilities

- **store** (`MapPlacesStoreContract`): owns the `places_in_bbox` RPC; returns raw
  rows. Only layer importing `getSupabaseClient`.
- **service** (`MapPlacesServiceContract`): fetch rows from store → map → rank
  (via `common/map.ranking.ts`) → enrich with saved state. Enrichment depends on
  the `SavedPlacesServiceContract` injected via constructor (imported from
  `saved-places/index.js`), **not** the concrete service. Preserve current
  behavior exactly: no user or empty result → mark all `isSaved:false`,
  `savedCollectionIds:[]`.
- **controller**: extract optional bearer token, resolve optional user
  (unauthenticated → `null` and no 401; token present but invalid → 401), parse
  query with zod, call service, write the response summary log, map `ZodError` →
  400 / else 500. Keep the `defineRoute`-style single OpenAPI route schema.
- **common**: `map.schemas.ts` gains response schemas (`MapPlace`,
  `MapPlacesResponse`) so DTO types become `z.infer` and OpenAPI is generated.

### Cross-module coupling

`map` already imports `SavedPlacesService` from `saved-places/index.js`. After
migration the dependency is injected into the map **service** (constructor) and
typed by the contract. `map.module.ts` wires `savedPlacesService` as the default,
and `app.ts` keeps the test override.

---

## Phase 2 — `me`

Single route `GET /me`; cleanest migration.

| Current | Target |
| --- | --- |
| `me.routes.ts` (token extract, auth, call service, log, error map) | `controllers/me.controller.ts` (reuse the `withUser` pattern) |
| `createMeService` / `MeService` (`me.service.ts`) | `services/me.service.ts` (keep contract; prefer a class `MeServiceImpl` for consistency, or keep the factory) |
| `supabaseProfileRepository.upsertDefaultProfile` → `profiles` upsert (`me.service.ts:29-50`) | `stores/me.store.ts` — rename `ProfileRepository` → `MeStoreContract` per convention (store, not repository) |
| `mapProfileRow` | `common/me.mappers.ts` (or keep in store if trivial) |
| hand-written `me.openapi.ts` (`MeUser`, `MeProfile`, `MeResponse`; `AuthErrorResponse` moved to Phase 0) | `common/me.schemas.ts` (zod) + `common/me.openapi.ts` (generated) |
| `me.routes.test.ts`, `me.service.test.ts` | `tests/` |

Add `index.ts` + `me.module.ts` (composition root) mirroring `saved-places`.

---

## Phase 3 — `health`

Smallest module; migrate for consistency but keep it thin.

| Current | Target |
| --- | --- |
| `health.routes.ts` (2 routes) | `controllers/health.controller.ts` |
| supabase ping `checkSupabaseConnection` (`lib/supabase.ts:25-37`) | `stores/health.store.ts` (the `places` head-count probe); `lib/supabase.ts` keeps only the client factory |
| trivial orchestration | `services/health.service.ts` (status assembly + calls store) |
| hand-written `health.openapi.ts` (`HealthStatusResponse`; `ErrorResponse` moved to Phase 0) | `common/health.schemas.ts` (zod) + `common/health.openapi.ts` (generated) |
| `health.routes.test.ts` | `tests/` |

Keep the injectable `supabaseHealthCheck` test seam — route it through the store
contract so tests still inject a fake without touching Supabase.

Add `index.ts` + `health.module.ts`.

---

## Phase 4 — `auth` (light touch, lower priority)

`auth` has no HTTP surface; it is a shared service used by `map`, `me`, and
`saved-places`. It already exposes a clean `AuthService` contract + singleton +
`extractBearerToken` util. Do **not** force a full controller/route shape on it.

Minimal alignment:

- Give it an `index.ts` so consumers import `AuthService`, `supabaseAuthService`,
  `AuthenticatedUser`, and `extractBearerToken` from `auth/index.js` (not the
  internal file).
- Move the Supabase call `getSupabaseClient().auth.getUser` (`auth.service.ts:14`)
  into `stores/auth.store.ts` so the "stores own DB access" rule holds; the
  service wraps the store and shapes `AuthenticatedUser`.
- Keep `extractBearerToken` as a pure util in `common/auth.tokens.ts`.

This phase is optional/last — ship it only if it does not churn the higher-value
module migrations. Record the decision either way.

---

## Phase 5 — Wiring (`app.ts`, `swagger.ts`)

- `src/app.ts`: swap each `registerXxxRoutes` for the module's
  `registerXxxModule` (composition root), keeping the same `prefix` and the same
  DI override options (`mapPlacesService`, `authService`, `meService`,
  `savedPlacesService`, `supabaseHealthCheck`). Update the imported types to come
  from each module's `index.js`.
- `src/config/swagger.ts`: register `httpErrorComponentSchemas` (Phase 0) plus
  each module's exported component list (`mapComponentSchemas`,
  `meComponentSchemas`, `healthComponentSchemas`, `savedPlacesComponentSchemas`)
  via a loop. Remove the per-symbol `addSchema` calls.

---

## Migration Order & Shippability

1. **Phase 0a** (`config/openapi.ts` generator) — foundation for all OpenAPI work.
2. **Phase 0b** (shared error schemas) — unblocks clean `$ref`s; touches `saved-places`.
3. **Phase 0c** (`src/http/` glue) + move `hasPostgresErrorCode` to `lib/`.
4. **Phase 0d** (retrofit `saved-places` onto the shared helpers) — proves them.
5. **Phase 1** (`map`) — highest value, frontend-critical.
6. **Phase 2** (`me`).
7. **Phase 3** (`health`).
8. **Phase 4** (`auth`) — optional/last.
9. **Phase 5** wiring is folded into each phase as that module lands.

Phase 0a–0d are infrastructure: land them before the module migrations so every
module consumes the shared helpers instead of copy-pasting. Each module is then
independently shippable: migrate one, keep its tests green, diff its OpenAPI,
commit. Do not rewrite everything in one change (per `TASKS_8`).

---

## Test Plan

Per module:

- Move existing tests into `tests/`; keep them green. They already build the app
  via `buildApp()` and inject fakes — the proven safety net for response shapes.
- Add a **service** unit test with a fake store contract; add a **store** test
  only where query behavior is risky (e.g. map RPC shaping).
- Controller tests keep asserting serialized response bodies (catches
  schema/serialization regressions from the zod→OpenAPI move).

Whole-app contract guard (run before and after each phase):

- Dump `/v1/swagger/openapi.json` (build the app in a tsx script, `app.inject` the
  route, write JSON to a file).
- Diff `components.schemas` ids and `paths` — **all ids and paths must be
  identical** (ids are the contract). Compare each migrated schema's `properties`
  and `required` arrays for parity.

Gate every phase:

```bash
cd backend
pnpm typecheck
pnpm build
pnpm test
pnpm lint
```

---

## Risks & Mitigations

- **Contract drift / renamed ids** → before/after `openapi.json` id diff; treat
  any added/removed id as a failure unless intentional.
- **Response serialization changes** (nullable as `nullable:true` vs `anyOf`) →
  use `target: "openapi-3.0"`; controller tests assert response shapes.
- **`.positive()` → boolean `exclusiveMinimum`** breaking Fastify serializer →
  use `.min(1)` for positive ints (documented gotcha from `saved-places`).
- **Map enrichment behavior** (optional auth, empty-result short-circuit) → unit
  test the service with/without user and with empty results; preserve the exact
  current branching from `map.routes.ts:126-149`.
- **Cross-module cycle** (`map` → `saved-places`) → depend on the
  `SavedPlacesServiceContract` from `index.js`; no internal imports.

---

## What Not To Do (per TASKS_8)

- No Clean Architecture ceremony, no global `repositories/` / `entities/` /
  `use-cases/`.
- No generic `utils/` or `shared/` dumping ground — split reusable code by
  responsibility (`lib/` adapters, `config/` wiring, `http/` glue).
- Keep `src/http/` glue minimal; do not over-abstract controllers into a rigid
  base — per-module domain-error maps stay in the module.
- Do not split a small file into many tiny files for aesthetics (health stays
  thin).
- Stores never make business decisions; services never see Fastify req/reply.
- Do not change endpoints, payload shapes, or OpenAPI ids in this task.

---

## Success Criteria

- Every product module follows the layered shape with a local `index.ts` and
  composition root.
- Supabase access exists only in `stores/` (and `lib/supabase.ts` factory).
- OpenAPI components are generated from zod in every module; shared error schemas
  have a single owner.
- `openapi.json` component ids and paths are unchanged across the whole migration.
- All tests pass; tests inject fakes without module mocking.
- `app.ts` and `swagger.ts` wire modules uniformly.

---

## Documentation Updates (after the work)

- `docs/CURRENT_STATE.md` — note all modules are now layered.
- `docs/DECISIONS.md` — record shared HTTP error schema home + auth decision.
- `docs/architecture/REPO_STRUCTURE.md` — add `src/http/`, the `config/openapi.ts`
  + `config/http-schemas.ts` homes, narrow `src/lib/` to "adapters only", and the
  explicit "no `shared/`/`utils/`" rule.
- `AGENTS.md` (root) — mirror the shared-code taxonomy in "Source Code Shape".
- `src/modules/saved-places/AGENTS.md` — note the OpenAPI generator and HTTP glue
  now live in `config/`/`http/` (shared), not inside the module.
- Update this file's status to Done and summarize lasting behavior elsewhere
  (task docs are historical by default).

## Assumptions

- Fastify + TypeScript + Supabase, single monolith, MVP speed still matters.
- No behavior or contract changes — pure structural + OpenAPI-source migration.
- User commits and pushes manually; agents migrate module-by-module.
