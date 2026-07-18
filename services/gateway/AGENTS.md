# AGENTS.md

Operational guide for agents and contributors working in this backend repo.

## Product One-Liner

Backend API for a taste-based city discovery MVP.

The product is not a Google Maps competitor. It is a city discovery assistant
that recommends places based on taste, lifestyle, and favorite-place patterns.

## Current Stack

- Node.js
- TypeScript
- Fastify
- Zod
- Supabase Postgres
- Redis
- Docker
- GitHub Actions
- Hetzner
- Self-hosted Grafana / Loki / Prometheus through the backend monorepo

The public API boundary is this Gateway service. Recommendation runtime work
lives in `../recommendation` and is called over the private Docker network by
HTTP. Do not add Kafka, RabbitMQ, or heavier service infrastructure unless it is
explicitly planned.

The backend-wide repository root is two levels up from here. Cross-service
deployment, compose, Nginx, observability, and load-test docs live in root
`docs/`, `deploy/`, and `load/`.

## Repo Map

```text
src/        application code
docs/       documentation and task plans
supabase/   database migrations
dumps/      small sample/import data files
scripts/    offline ETL and source integration mappers
```

Start with:

- `../../docs/ARCHITECTURE.md`
- `../../docs/DEPLOYMENT.md`
- `docs/CURRENT_STATE.md`
- `README.md`
- `docs/README.md`
- `docs/architecture/REPO_STRUCTURE.md`

## Source Code Shape

Keep feature code inside `src/modules/<feature>/`.

New feature modules should use the lightweight layered OOP module pattern.
`src/modules/saved-places/AGENTS.md` is the current reference implementation.

```text
src/modules/<feature>/
  AGENTS.md                 optional for complex modules
  index.ts                  public module entrypoint
  <feature>.module.ts       composition root / dependency wiring
  controllers/              Fastify HTTP layer
  services/                 business logic / orchestration
  stores/                   Supabase/data access
  common/                   types, errors, mappers, schemas, openapi
  tests/                    controller/service/store tests
```

Use this pattern for new or rewritten product modules:

- `cities`
- `places`
- `onboarding`
- `saved-places`
- `recommendations`

Layer rules:

- dependencies point inward: `controller -> service -> store`;
- controllers parse HTTP input, call services, map domain errors to HTTP;
- services contain business logic and depend on store contracts, not Supabase;
- stores are the only layer that talks to Supabase/database APIs;
- `index.ts` is the public import surface for other modules;
- prefer constructor injection for collaborators;
- keep request/response schemas in `common/<feature>.schemas.ts`;
- generate OpenAPI components from schemas where practical;
- do not import another module's internals when its `index.ts` exports what you need.

Shared code is split by responsibility — there is no `shared/` or `utils/`
bucket:

- `src/lib/` — infrastructure adapters only (Supabase client, future clients);
- `src/config/` — app wiring (env, logger, routes, swagger), plus the
  `openapi.ts` zod→component generator and `http-schemas.ts` shared error schemas;
- `src/http/` — Fastify glue every controller reuses (`docsRoute`,
  `handleCommonError`, `createAuthGuard`, `logResponseSummary`).

All product modules (`map`, `me`, `health`, `saved-places`) now use the layered
pattern; `auth` stays a shared service (no HTTP) with its DB call in a store.
New modules should follow the layered pattern from the start.

## Commands

Use pnpm.

```bash
pnpm dev
pnpm build
pnpm test
pnpm lint
pnpm typecheck
pnpm map:tripadvisor dumps/raw_tripadvisor_restaurants_import.csv --out dumps/tripadvisor_places.csv
pnpm map:osm dumps/bucharest_cafes.csv --out dumps/osm_bucharest_places.csv
```

Before finishing backend code changes, run:

```bash
pnpm build
pnpm test
pnpm lint
```

Docs-only changes do not need the full test suite unless they touch generated or
validated artifacts.

## Current Runtime

Production:

```text
https://sloco.pp.ua
```

Useful checks:

```bash
curl https://sloco.pp.ua/v1/health
curl https://sloco.pp.ua/v1/health/supabase
curl "https://sloco.pp.ua/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

## Important API

Current frontend-facing map endpoint:

```http
GET /v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
```

Current saved places endpoints:

```http
GET /v1/me/saved
GET /v1/me/saved/collections/:collectionId
POST /v1/me/saved/places
DELETE /v1/me/saved/places/:placeId
POST /v1/me/saved/collections
PATCH /v1/me/saved/collections/:collectionId
DELETE /v1/me/saved/collections/:collectionId
POST /v1/me/saved/collections/:collectionId/places
DELETE /v1/me/saved/collections/:collectionId/places/:placeId
PATCH /v1/me/saved/collections/:collectionId/places/order
```

Contract docs:

```text
https://sloco.pp.ua/v1/swagger/openapi.json
docs/FRONTEND_MAP_API.md
```

Map endpoint payloads should stay lightweight. Full place details should be a
separate endpoint later.

## Database

Supabase is the managed Postgres provider.

Current serving table:

```text
public.places
public.saved_places
public.saved_collections
public.saved_collection_places
```

Migration files:

```text
supabase/migrations/
```

Do not commit Supabase service role keys or other secrets.

### Migration Restraint Rule

Do not add a database migration unless the change genuinely needs Postgres to
change — a new table, column, index, constraint, or a changed stored function
(RPC) body. The schema is already many migrations deep; each migration is a
permanent, ordered, run-once artifact and the count should not grow casually.

Before writing a migration, check whether the same result can be achieved in the
application layer instead:

- new read shape → change the query (`select`, filters, ordering) or the mapper;
- windowing / slicing that fits within existing query limits → do it in the
  gateway, not a new migration;
- derived or computed fields → compute in the service/mapper.

Changing an existing RPC body (e.g. raising a hardcoded `limit`) *is* a legitimate
migration when that limit genuinely blocks a feature — the point is not to avoid
migrations dogmatically, but to not reach for one when an app-layer change works.

Reach for a migration only when none of the above can express the change. When in
doubt, prefer the query/app-layer change and leave the schema alone.

### Destructive SQL Warning Rule

When asking the user to run any SQL/migration that contains destructive or
high-risk operations, warn in CAPS before the command/instructions.

High-risk operations include:

- `DROP TABLE`;
- `DROP FUNCTION`;
- `TRUNCATE TABLE`;
- mass `DELETE`;
- `ALTER TABLE` that can rewrite/drop/change existing data or constraints.

Example warning:

```text
ВАЖНО: ЭТА МИГРАЦИЯ DESTRUCTIVE. ОНА ДЕЛАЕТ TRUNCATE/DROP/ALTER И МОЖЕТ
УДАЛИТЬ ИЛИ ИЗМЕНИТЬ ДАННЫЕ.
```

Explain exactly which tables/functions/data are affected before telling the
user to run it.

## Logging And Grafana

Backend logs are structured JSON in production.

Request and response logs should be easy to distinguish:

- request logs use `eventType: "request"` and `REQUEST ...` messages;
- response summary logs use `eventType: "response"` and `RESPONSE ...`
  messages.

Grafana dashboards and observability config live at the monorepo root (not in this
service):

```text
../../observability/grafana/dashboards/app/backend-logs.json
../../observability/README.md
```

Do not log large response bodies. Log compact summaries.

## Git Rules

The user commits and pushes manually.

Agents should:

- edit files when asked;
- run checks when appropriate;
- suggest a commit message at the end;
- never commit automatically;
- never push automatically.

## Documentation Rules

Use:

- `docs/README.md` as the docs index;
- `docs/CURRENT_STATE.md` as the current project snapshot;
- `docs/DECISIONS.md` as the compact decision log;
- `docs/tasks/README.md` as the task index;
- `docs/architecture/REPO_STRUCTURE.md` for "where does this go?";
- `dumps/README.md` for sample/import data rules;
- root `../../observability/README.md` for Grafana dashboard/provisioning rules;
- root `../../docs/` for backend-platform architecture/deploy/runbook docs.

Update `AGENTS.md` only when:

- repo structure changes;
- commands change;
- architecture conventions change;
- repeated agent confusion shows missing context.

Do not update `AGENTS.md` for every small feature.

## Swagger Direction

Swagger/OpenAPI direction:

- keep schemas near modules;
- use Zod as the request/response source of truth where practical;
- generate JSON Schema/OpenAPI components from module schemas instead of
  maintaining hand-written duplicates;
- keep route OpenAPI helpers in `common/<feature>.openapi.ts`;
- register module component schemas in `src/config/swagger.ts`;
- expose generated docs at `/v1/swagger/docs` and `/v1/swagger/openapi.json`.
