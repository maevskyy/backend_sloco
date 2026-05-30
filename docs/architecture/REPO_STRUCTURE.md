# Repo Structure

This file answers: where should new things go?

## Top-Level Folders

```text
src/        application code
docs/       human docs, task plans, API notes, runbooks
deploy/     production deploy templates and nginx config
supabase/   database migrations and Supabase-owned setup
grafana/    dashboard-as-code files and Grafana notes
dumps/      small import/sample data files
scripts/    offline ETL and source integration mappers
```

## `src/`

Application code lives here.

Current shape:

```text
src/
  app.ts
  server.ts
  config/
  lib/
  modules/
```

Rules:

- Feature code goes into `src/modules/<feature>/`.
- Cross-cutting runtime config goes into `src/config/`.
- Small infrastructure adapters go into `src/lib/`.
- Do not create generic `utils/` until there is repeated real usage.

Module pattern:

```text
src/modules/map/
  map.routes.ts
  map.schemas.ts
  map.service.ts
  map.routes.test.ts
```

Add more files only when useful:

- `*.repository.ts` for complex database access;
- `*.mapper.ts` for noisy response/domain mapping;
- `*.types.ts` for types shared across multiple files;
- `src/clients/` for external HTTP/service clients.

## `docs/`

Docs are split by purpose, not by random topic.

Current state:

- task files live in `docs/tasks/`;
- `docs/README.md` is the navigation index;
- `docs/tasks/README.md` indexes task plans;
- `docs/architecture/` is for stable engineering docs.

Future target:

```text
docs/
  api/
  runbooks/
  tasks/
  architecture/
```

Do not move many docs at once unless the commit is docs-only.

## `deploy/`

Deployment templates live here.

Current files:

- Docker Compose production template.
- Nginx production config.

Runtime secrets do not live here.

## `supabase/`

Supabase-owned database changes live here.

Rules:

- migration files go into `supabase/migrations/`;
- do not commit Supabase service role keys;
- manual SQL changes should eventually become migration files;
- raw staging tables are allowed during MVP, but final domain tables should be
  planned separately.

## `grafana/`

Grafana dashboard-as-code lives here.

Rules:

- dashboard JSON files go into `grafana/dashboards/`;
- dashboard files must not contain tokens, passwords, or Grafana Cloud URLs;
- import dashboards manually for MVP;
- document dashboard import/update flow in `grafana/README.md`.

## `dumps/`

Small local import/sample data lives here.

Rules:

- keep useful MVP import files only;
- do not commit private user data;
- do not commit large raw exports by default;
- document where a generated dump came from.

## `scripts/`

Offline developer/operator scripts live here.

Current shape:

```text
scripts/
  integrations/
    _shared/
    tripadvisor/
    osm/
```

Rules:

- keep runtime API code in `src/`, not in `scripts/`;
- one provider mapper per `scripts/integrations/<provider>/`;
- shared mapper contracts/helpers live in `scripts/integrations/_shared/`;
- mapper output should match the canonical `public.places` import columns;
- generated import files go into `dumps/` and should be documented.

## Swagger / OpenAPI

Future Swagger setup should not become a second source of truth.

Recommended shape:

```text
src/config/swagger.ts
src/modules/*/*.schemas.ts
docs/api/
```

The route schema should drive validation and OpenAPI output as much as possible.
