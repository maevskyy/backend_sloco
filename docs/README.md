# Backend Docs

This folder is the main navigation point for backend documentation.

## Current Service

- Product: taste-based city discovery backend.
- Runtime: Node.js, TypeScript, Fastify.
- Database: Supabase Postgres.
- Production URL:

  ```text
  http://52.18.13.69
  ```

## Start Here

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

## API Docs

- Frontend map endpoint contract:
  ```text
  FRONTEND_MAP_API.md
  ```

Current note:

- Markdown API docs are usage docs.
- Future Swagger/OpenAPI should be generated from route schemas, not maintained
  as a separate hand-written source of truth.

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
- API examples and frontend handoff live in docs.
- Database DDL lives in `supabase/migrations/`.
- Deployment runtime files live in `deploy/`.
- Grafana dashboard JSON lives in `grafana/dashboards/`.
- Data import files live in `dumps/`.
- Product and agent conventions live in `AGENTS.md`.
