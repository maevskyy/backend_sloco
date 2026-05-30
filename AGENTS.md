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
- Docker
- GitHub Actions
- Hetzner
- Grafana Cloud Loki

Do not add Redis, Kafka, microservices, or heavy architecture unless there is a
real bottleneck.

## Repo Map

```text
src/        application code
docs/       documentation and task plans
deploy/     production deploy templates
supabase/   database migrations
grafana/    dashboard JSON and Grafana notes
dumps/      small sample/import data files
scripts/    offline ETL and source integration mappers
```

Start with:

- `docs/CURRENT_STATE.md`
- `README.md`
- `docs/README.md`
- `docs/architecture/REPO_STRUCTURE.md`

## Source Code Shape

Keep feature code inside `src/modules/<feature>/`.

Current module pattern:

```text
src/modules/map/
  map.routes.ts
  map.schemas.ts
  map.service.ts
  map.routes.test.ts
```

Use this pattern for new modules:

- `cities`
- `places`
- `onboarding`
- `saved-places`
- `recommendations`

Add extra files only when needed:

- `*.repository.ts` for complex database queries;
- `*.mapper.ts` for noisy mapping;
- `*.types.ts` for shared module types;
- `src/clients/` for external services like the future Python scoring service.

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
http://65.108.142.55
```

Useful checks:

```bash
curl http://65.108.142.55/v1/health
curl http://65.108.142.55/v1/health/supabase
curl "http://65.108.142.55/v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13"
```

## Important API

Current frontend-facing map endpoint:

```http
GET /v1/map/places?swLat=52.4800&swLng=13.3300&neLat=52.5600&neLng=13.4700&zoom=13
```

Contract docs:

```text
http://65.108.142.55/v1/swagger/openapi.json
docs/FRONTEND_MAP_API.md
```

Map endpoint payloads should stay lightweight. Full place details should be a
separate endpoint later.

## Database

Supabase is the managed Postgres provider.

Current serving table:

```text
public.places
```

Migration files:

```text
supabase/migrations/
```

Do not commit Supabase service role keys or other secrets.

## Logging And Grafana

Backend logs are structured JSON in production.

Request and response logs should be easy to distinguish:

- request logs use `eventType: "request"` and `REQUEST ...` messages;
- response summary logs use `eventType: "response"` and `RESPONSE ...`
  messages.

Grafana dashboard JSON:

```text
grafana/dashboards/backend-logs.json
```

Grafana notes:

```text
grafana/README.md
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
- `grafana/README.md` for dashboard import/update rules.

Update `AGENTS.md` only when:

- repo structure changes;
- commands change;
- architecture conventions change;
- repeated agent confusion shows missing context.

Do not update `AGENTS.md` for every small feature.

## Swagger Direction

Swagger/OpenAPI should be a separate task.

Preferred direction:

- keep schemas near modules;
- avoid hand-written OpenAPI as a second source of truth;
- expose generated docs locally first;
- decide production exposure later.
