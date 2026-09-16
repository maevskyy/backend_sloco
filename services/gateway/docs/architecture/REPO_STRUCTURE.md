# Gateway Repo Structure

This file answers: where should new Gateway things go?

For backend-wide repository layout, deployment, observability, and service
boundaries, use the root docs:

```text
../../../docs/ARCHITECTURE.md
../../../docs/DEPLOYMENT.md
```

## Service Folders

```text
src/                 Gateway application code
docs/                Gateway API docs, decisions, and historical task plans
supabase/migrations/ database migrations owned by the Gateway
supabase/rollback/   undo scripts for migrations that replace RPC signatures
grafana/             dashboard JSON and provisioning files
dumps/               small Gateway import/sample files
scripts/             offline Gateway ETL and import scripts
```

`supabase/rollback/` holds hand-written undo scripts, one per risky migration —
see its `README.md` for when a migration needs one and the rules a rollback
follows. They are never run as part of the migration sequence.

Infrastructure that belongs to the whole backend stack lives at repo root:

```text
deploy/
docker-compose.yml
load/
.github/workflows/
```

## `src/`

Application code lives here.

```text
src/
  app.ts
  server.ts
  config/      app wiring: env, logger, routes, swagger, shared HTTP schemas
  http/        Fastify HTTP glue, module-agnostic
  lib/         infrastructure adapters
  modules/     feature modules
  observability/
```

Rules:

- Feature code goes into `src/modules/<feature>/`.
- Cross-cutting runtime config and wiring goes into `src/config/`.
- Reusable Fastify glue goes into `src/http/`.
- Infrastructure adapters go into `src/lib/`.
- Do not create a generic `utils/` or `shared/` dumping ground.

## Module Pattern

```text
src/modules/<feature>/
  AGENTS.md                 optional local module guide
  index.ts                  public entrypoint for other modules
  <feature>.module.ts       composition root / dependency wiring
  controllers/              Fastify HTTP layer
  services/                 business logic / orchestration
  stores/                   Supabase/data access
  common/                   types, errors, mappers, schemas, openapi
  tests/                    controller/service/store tests
```

Rules:

- `src/modules/saved-places/` is the reference implementation.
- Dependencies point inward: `controllers -> services -> stores`.
- Controllers own HTTP concerns only.
- Services own business logic and depend on store contracts/interfaces.
- Stores are the only layer that talks to Supabase/database APIs.
- Other modules import through `src/modules/<feature>/index.ts`, not internals.

## Docs

Gateway docs are split by purpose:

```text
docs/
  architecture/
  tasks/
  FRONTEND_*.md
  CURRENT_STATE.md
  DECISIONS.md
```

Root `docs/` is for backend-platform docs. Gateway `docs/` is for Gateway
runtime/API behavior.

## Grafana

Grafana files live here because current dashboards are Gateway/application
dashboards:

```text
grafana/dashboards/
grafana/provisioning/
```

They are mounted by root `docker-compose.yml`.

## Scripts And Dumps

Offline import and ETL scripts stay in `scripts/`. Generated/small import files
can live in `dumps/` when documented. Large private/raw datasets should not be
committed.

## Swagger / OpenAPI

Swagger/OpenAPI should not become a second source of truth.

Recommended shape:

```text
src/config/swagger.ts
src/modules/<feature>/common/<feature>.schemas.ts
src/modules/<feature>/common/<feature>.openapi.ts
docs/api/
```

Rules:

- Zod request/response schemas are the preferred source of truth.
- Module OpenAPI files generate JSON Schema components where practical.
- Route schemas reference generated components with stable ids.
- `src/config/swagger.ts` registers cross-module components and exposes the
  generated OpenAPI document.
