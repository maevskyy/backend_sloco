# Backend Docs

This folder is the main navigation point for backend documentation.

## Current Service

- Product: taste-based city discovery backend.
- Runtime: Node.js, TypeScript, Fastify.
- Database: Supabase Postgres.
- Production URL:

  ```text
  http://65.108.142.55
  ```

## Start Here

- Current project snapshot:
  ```text
  CURRENT_STATE.md
  ```

- Local development and project overview:
  ```text
  ../README.md
  ```

- Agent / contributor operating guide:
  ```text
  ../AGENTS.md
  ```

- Repository structure rules:
  ```text
  architecture/REPO_STRUCTURE.md
  ```

- Current decision log:
  ```text
  DECISIONS.md
  ```

## API Docs

- Swagger UI:
  ```text
  http://65.108.142.55/v1/swagger/docs
  ```

- OpenAPI JSON for frontend agents:
  ```text
  http://65.108.142.55/v1/swagger/openapi.json
  ```

- Frontend map endpoint contract:
  ```text
  FRONTEND_MAP_API.md
  ```

Current note:

- Markdown API docs are usage docs.
- Swagger/OpenAPI is generated from route schemas, not maintained as a separate
  hand-written source of truth.

## Operations Docs

- Production deployment:
  ```text
  DEPLOYMENT.md
  ```

- Grafana dashboard files:
  ```text
  ../grafana/README.md
  ```

- Supabase migrations:
  ```text
  ../supabase/migrations/
  ```

## Task Plans

Task plans live in:

```text
tasks/
```

Task index:

```text
tasks/README.md
```

New task docs should also be created in `docs/tasks/`.

## Source Of Truth Rules

- Runtime behavior lives in `src/`.
- Current project truth lives in `CURRENT_STATE.md`.
- Current durable decisions live in `DECISIONS.md`.
- API examples and frontend handoff live in docs.
- Database DDL lives in `supabase/migrations/`.
- Deployment runtime files live in `deploy/`.
- Grafana dashboard JSON lives in `grafana/dashboards/`.
- Data import files live in `dumps/`.
- Offline ETL and source mappers live in `scripts/`.
- Product and agent conventions live in `AGENTS.md`.
