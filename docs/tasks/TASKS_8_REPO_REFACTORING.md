# TASKS 8: Repo Structure Refactoring Plan

## Summary

The repository is in good shape for an MVP backend.

The `src` structure is clean, small, and easy to navigate:

```text
src/
  app.ts
  server.ts
  config/
  lib/
  modules/
```

The current pain is not application architecture. The current pain is project
navigation around the code:

- docs are growing quickly;
- task docs and long-term docs live in the same flat folder;
- operational assets exist in multiple top-level folders;
- data dumps are tracked without a clear rule;
- `AGENTS.md` is useful, but already mixes product context, architecture,
  roadmap, API ideas, and old implementation direction.

Do not do a deep code refactor right now. The repo is still small. The highest
value refactor is to make project knowledge easier to find and maintain.

## Current Assessment

### What is good

- `src/modules/*` is a good shape for Fastify feature modules.
- Tests live next to the module they validate, which is easy to maintain.
- `config/` contains cross-cutting runtime concerns like env and logger.
- `lib/supabase.ts` is a simple infrastructure adapter.
- `deploy/`, `supabase/`, `grafana/`, and `dumps/` being outside `src` is the
  right instinct.
- CI/CD, Docker, Supabase, and Grafana are already separated from app logic.

### What is starting to hurt

- `docs/` is flat and already has 2k+ lines.
- `TASKS_N_*.md` files are useful, but they are not the same thing as permanent
  docs.
- `README.md` is too thin to onboard a new teammate or agent.
- `AGENTS.md` is valuable but too broad. It needs a stable repo map and fewer
  outdated implementation details.
- `dumps/` needs rules before real 7k+ datasets and future exports pile up.
- Grafana dashboard JSON is useful, but should have a short README explaining
  import/update flow.
- Supabase migrations are okay, but we should document how manual dashboard SQL
  changes and migration files relate.

### Swagger readiness

Adding Swagger is straightforward, because routes are already modular.

The important choice is documentation ownership:

- Swagger/OpenAPI should be generated from route schemas.
- Markdown API docs should explain usage and examples, not duplicate every
  schema field forever.

Recommended future approach:

- keep Zod as the source of request validation;
- add OpenAPI generation with Fastify Swagger tooling;
- either introduce a Zod-to-OpenAPI bridge or define Fastify JSON schemas near
  each route;
- keep endpoint docs next to modules when possible.

Do not add Swagger as a separate hand-written OpenAPI file unless we have a
strong reason. It will drift.

## Refactoring Plan

### 1. Add a docs index

Create:

```text
docs/README.md
```

It should be the table of contents for humans and agents:

- current production URL;
- where deployment docs live;
- where frontend API docs live;
- where task plans live;
- where Grafana dashboard docs live;
- where database setup lives;
- which docs are current source of truth.

This is the first thing a teammate or agent should open after `README.md`.

### 2. Split docs by purpose

Move toward this structure:

```text
docs/
  README.md
  api/
    FRONTEND_MAP_API.md
  runbooks/
    DEPLOYMENT.md
    GRAFANA_LOGS.md
  tasks/
    TASKS_1_CI.md
    TASKS_2_CD.md
    ...
  architecture/
    REPO_STRUCTURE.md
    BACKEND_ARCHITECTURE.md
```

Implemented first step:

- add `docs/README.md`;
- add `docs/tasks/README.md`;
- move task files into `docs/tasks/`.

### 3. Tighten `AGENTS.md`

`AGENTS.md` should be a stable agent operating manual, not a full product spec.

Keep:

- product one-liner;
- current tech stack;
- repo map;
- coding rules;
- test/build commands;
- deploy warning: user commits and pushes manually;
- where to find docs;
- current important conventions.

Move or summarize:

- long product explanation;
- old API wishlist;
- large database design section;
- old implementation order.

Update frequency:

- update `AGENTS.md` when repo structure changes;
- update it when commands change;
- update it when architectural rules change;
- do not update it for every feature or every task.

Good cadence:

- after every major milestone;
- after adding a new subsystem like Swagger/Auth/Saves;
- when an agent repeatedly needs context that is not obvious from files.

### 4. Add permanent repo structure docs

Create:

```text
docs/architecture/REPO_STRUCTURE.md
```

This should describe:

- what belongs in `src/`;
- what belongs in `docs/`;
- what belongs in `deploy/`;
- what belongs in `supabase/`;
- what belongs in `grafana/`;
- what belongs in `dumps/`;
- naming rules for new modules and tasks.

Keep it short. It should answer "where do I put this?".

### 5. Add data dump rules

Create:

```text
dumps/README.md
```

Rules:

- small sample/import CSVs can be committed if useful;
- real large dumps should not be committed by default;
- generated import-ready files should include source and purpose;
- no secrets or private user data in dumps;
- if a file is temporary, name it clearly.

Consider later:

```text
dumps/.gitignore
```

For example:

```gitignore
*.csv
!sample_with_coordinates.csv
!raw_tripadvisor_restaurants_import.csv
!README.md
```

Do not add this blindly if we still want to commit curated seed files.

### 6. Add Grafana docs beside dashboards

Create:

```text
grafana/README.md
```

It should explain:

- which dashboard JSON files exist;
- how to import into Grafana Cloud;
- which datasource to select;
- how to update dashboards safely;
- why dashboard JSON has no tokens or URLs.

This prevents `grafana/dashboards/*.json` from becoming mysterious config.

### 7. Prepare Swagger cleanly

Swagger should be its own task, but repo refactor should make it easy.

Recommended future files:

```text
src/config/swagger.ts
src/modules/*/*.schemas.ts
docs/api/
```

Rules:

- schemas should live near modules;
- request validation and OpenAPI schemas should not become two separate truths;
- Swagger UI should be enabled for local/staging first;
- production exposure can be decided later;
- generated OpenAPI JSON can be exposed at `/docs/json` or `/openapi.json`.

### 8. Keep `src` mostly as-is for now

Do not split the backend into layers just because the repo exists.

Current module style is good enough:

```text
src/modules/map/
  map.routes.ts
  map.schemas.ts
  map.service.ts
  map.routes.test.ts
```

Future modules should follow the same pattern:

```text
src/modules/places/
src/modules/cities/
src/modules/onboarding/
src/modules/saved-places/
```

Add more structure only when needed:

- `*.repository.ts` when database queries become complex;
- `*.mapper.ts` when response mapping becomes noisy;
- `*.types.ts` when types are shared across multiple files;
- `src/clients/` when external services appear, like Python scoring.

### 9. Improve root onboarding

Update `README.md` after docs are organized.

It should include:

- what this service is;
- local setup;
- env vars;
- common commands;
- local health checks;
- link to docs index;
- production deploy note;
- current public API docs link.

Keep README short. It should point to deeper docs instead of becoming a giant
manual.

## Suggested Execution Order

1. Create `docs/README.md`.
2. Create `docs/tasks/README.md`.
3. Create `docs/architecture/REPO_STRUCTURE.md`.
4. Create `dumps/README.md`.
5. Create `grafana/README.md`.
6. Trim and update `AGENTS.md`.
7. Update root `README.md`.
8. Decide whether to move existing docs into subfolders in one separate
   documentation-only commit.
9. Create a separate Swagger task after the docs structure is stable.

## What Not To Refactor Yet

Do not do these now:

- do not introduce Clean Architecture folders;
- do not add repositories for every module by default;
- do not move Supabase queries away from services until they get complex;
- do not add a generic shared error framework yet;
- do not create `utils/` as a dumping ground;
- do not add Swagger in the same commit as docs restructuring;
- do not move every file at once if it makes current branches painful.

## Success Criteria

After this refactor:

- a new teammate can understand the repo in 10 minutes;
- an agent knows where to read and where to write;
- task docs are separated from permanent docs;
- adding Swagger has an obvious place;
- adding a new feature module has an obvious local pattern;
- data dumps and Grafana dashboards are no longer mysterious top-level folders;
- `AGENTS.md` becomes shorter, more current, and more operational.

## Assumptions

- We keep Fastify + TypeScript + Supabase.
- We keep the current feature-module style in `src`.
- User commits and pushes manually.
- Current priority is MVP speed with enough structure to avoid future mess.
- Refactor should be documentation/organization first, not application rewrites.
