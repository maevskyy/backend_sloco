# TASKS 8: Repo And Module Architecture Refactoring

## Summary

The repo started with a simple flat Fastify module style. That was good for the
first MVP endpoints, but the saved places feature showed the limit: routes,
business logic, data access, schemas, OpenAPI, mappers, and tests quickly became
too dense in a few large files.

The new target architecture is a **lightweight layered OOP module**. It keeps the
backend as a simple Fastify monolith, but gives each product module a clear local
shape.

Reference implementation:

```text
src/modules/saved-places/
```

Local module guide:

```text
src/modules/saved-places/AGENTS.md
```

## Current Decision

Use this shape for new or rewritten product modules:

```text
src/modules/<feature>/
  AGENTS.md                 optional for complex modules
  index.ts                  public module entrypoint
  <feature>.module.ts       composition root / dependency wiring
  controllers/              Fastify HTTP layer
  services/                 business logic / orchestration
  stores/                   Supabase/data access
  common/                   module-local shared files
  tests/                    module tests
```

The dependency direction is:

```text
controller -> service -> store
```

Inner layers do not import outer layers.

## Layer Responsibilities

### `index.ts`

Public import surface for the module.

Other modules should import from:

```ts
import { savedPlacesService } from "../saved-places/index.js";
```

They should not import from:

```ts
../saved-places/services/...
../saved-places/stores/...
../saved-places/common/...
```

### `<feature>.module.ts`

Composition root.

Responsibilities:

- create the controller;
- wire default services/stores/auth clients;
- accept test overrides through options;
- register the controller with Fastify.

### `controllers/`

HTTP layer only.

Responsibilities:

- register Fastify routes;
- parse request input with Zod;
- call services;
- map domain errors to HTTP status codes;
- write compact response logs;
- never talk to Supabase directly;
- avoid business rules.

### `services/`

Business logic / orchestration.

Responsibilities:

- implement product workflows;
- enforce business rules;
- throw domain errors;
- depend on store contracts/interfaces;
- stay free of Fastify request/reply types;
- stay free of Supabase client calls.

### `stores/`

Data access.

Responsibilities:

- talk to Supabase/database APIs;
- own SQL/RPC/table query details;
- return rows or mapped domain DTOs;
- avoid HTTP concerns;
- avoid business policy decisions.

### `common/`

Module-local shared building blocks.

Allowed contents:

- `<feature>.schemas.ts`
- `<feature>.openapi.ts`
- `<feature>.types.ts`
- `<feature>.errors.ts`
- `<feature>.mappers.ts`

Do not create many one-file folders like `constants/`, `dto/`, `mappers/`, or
`errors/` until there is real pressure. `common/` is the intentionally small
bucket for module-local shared pieces.

### `tests/`

Module tests live inside the module.

Recommended split:

- controller tests build the app and inject fake services/auth;
- service tests inject fake stores;
- store tests are optional and should be added when query behavior becomes risky.

## OpenAPI Direction

Zod should be the preferred source of truth for request/response shapes.

Recommended module files:

```text
common/<feature>.schemas.ts
common/<feature>.openapi.ts
```

Rules:

- define request and response Zod schemas once;
- register schemas with stable ids;
- generate JSON Schema/OpenAPI components from the registry where practical;
- keep route schema definitions small with shared helpers;
- register module components in `src/config/swagger.ts`;
- do not maintain a separate hand-written OpenAPI contract that can drift.

The `saved-places` module demonstrates this via:

```text
common/saved-places.schemas.ts
common/saved-places.openapi.ts
```

## Migration Plan

Do not rewrite every file in one huge change. Migrate module-by-module.

Suggested order:

1. Keep `saved-places` as the reference.
2. Migrate `map` next, because it is frontend-critical and already has ranking,
   saved-state enrichment, Supabase RPC access, schemas, OpenAPI, and tests.
3. Migrate `me` / auth-adjacent modules if they start growing.
4. Use the new pattern for future `places`, `recommendations`, `taste`, and
   onboarding modules.

Existing flat modules can stay flat until they are actively touched for
meaningful work. When touched, prefer moving them toward the layered pattern.

## What Not To Do

- Do not introduce full Clean Architecture ceremony.
- Do not add global `repositories/`, `entities/`, or `use-cases/` folders.
- Do not create a generic `utils/` dumping ground.
- Do not split one 150-line file into ten tiny files only for aesthetics.
- Do not make stores responsible for business decisions.
- Do not make services know about Fastify request/reply.
- Do not import another module's internals when `index.ts` exports the contract.

## Documentation Updates

Stable architecture docs that should follow this standard:

- `AGENTS.md`
- `docs/architecture/REPO_STRUCTURE.md`
- `docs/DECISIONS.md`
- module-local `AGENTS.md` files for complex modules

Task plans are historical by default. Update a task file only when it is still
used as active architecture guidance.

## Success Criteria

- A new module has an obvious local structure.
- A module can be understood without jumping through the whole repo.
- Tests can inject fake dependencies without module mocking.
- OpenAPI and runtime validation do not drift.
- Supabase access is isolated in stores.
- Business logic is readable in services.
- Controllers stay focused on HTTP.
- Agents can follow module-local `AGENTS.md` and avoid inventing a new style.

## Assumptions

- We keep Fastify + TypeScript + Supabase.
- We keep a simple monolith, not microservices.
- User commits and pushes manually.
- MVP speed still matters; this architecture is intentionally thin.
