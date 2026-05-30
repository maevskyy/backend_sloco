# TASKS 1: GitHub Actions CI

## Goal

Add a proper baseline CI for the backend repository.

The CI should run on every branch push and every pull request, not only on `main`. It should keep the backend honest with three independent checks:

- Build
- Test
- Lint

These checks must run in parallel as separate GitHub Actions jobs.

## Context

The backend is a Node.js + TypeScript + Fastify API managed with `pnpm`.

The repository is public, so standard GitHub-hosted runners are free for GitHub Actions.

Current useful scripts:

```bash
pnpm typecheck
pnpm build
```

Missing scripts/tools that this task should add:

```bash
pnpm lint
pnpm test
```

## CI Standard

Use three separate jobs:

```text
build
test
lint
```

They should not depend on each other.

This means no `needs:` chain between them. GitHub Actions will run them in parallel automatically.

## Scope

In scope:

- Add a GitHub Actions workflow.
- Run on every `push`.
- Run on every `pull_request`.
- Add linting.
- Add a test runner.
- Add at least one basic test so the test pipeline is real.
- Install dependencies with `pnpm install --frozen-lockfile`.
- Run `build`, `test`, and `lint` as separate parallel CI jobs.

Out of scope for this task:

- Deployment.
- Docker image build.
- Supabase migrations.
- Integration tests against real Supabase.
- Test coverage thresholds.
- Environment-specific secrets.
- CD pipeline.

## Proposed Tooling

Use:

- ESLint for linting.
- Vitest for tests.

Reasoning:

- ESLint is the standard linting tool for TypeScript projects.
- Vitest is lightweight, fast, and works well with TypeScript/ESM.

## Files To Change

Expected files:

```text
package.json
.github/workflows/ci.yml
eslint.config.js
src/**/*.test.ts
```

Exact test file can be decided during implementation.

## Required Scripts

Add or confirm these scripts in `package.json`:

```json
{
  "scripts": {
    "build": "tsc",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run"
  }
}
```

Build job can run either:

```bash
pnpm build
```

or:

```bash
pnpm typecheck
pnpm build
```

Prefer running both `typecheck` and `build` in the build job so CI catches type errors and production compilation issues.

## Workflow Triggers

The workflow should run on all branches:

```yaml
on:
  push:
  pull_request:
```

No branch filter.

## Workflow Shape

Use one workflow file:

```text
.github/workflows/ci.yml
```

Expected structure:

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup pnpm
      - setup node
      - install
      - run typecheck
      - run build

  test:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup pnpm
      - setup node
      - install
      - run tests

  lint:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - setup pnpm
      - setup node
      - install
      - run lint
```

The repeated setup is acceptable for the first CI version. Optimize later only if it becomes slow or annoying.

## Implementation Steps

1. Add ESLint dependencies.

2. Add Vitest dependency.

3. Add `lint` and `test` scripts to `package.json`.

4. Add ESLint config.

5. Add one minimal test.

   Preferred first target:

   - `GET /health` returns `{ "status": "ok" }`

6. Create `.github/workflows/ci.yml`.

7. Configure workflow triggers with no branch filters.

8. Configure three separate jobs:

   - `build`
   - `test`
   - `lint`

9. Ensure the jobs do not use `needs:` and therefore run in parallel.

10. Run locally:

    ```bash
    pnpm build
    pnpm test
    pnpm lint
    ```

11. Review `git status` and diff.

12. User handles commit and push manually.

## Acceptance Criteria

- Workflow exists at `.github/workflows/ci.yml`.
- Workflow runs on every push.
- Workflow runs on every pull request.
- Workflow has separate `build`, `test`, and `lint` jobs.
- The three jobs do not depend on each other.
- Build job runs typecheck and build.
- Test job runs `pnpm test`.
- Lint job runs `pnpm lint`.
- At least one real test exists.
- `pnpm build` passes locally.
- `pnpm test` passes locally.
- `pnpm lint` passes locally.
- No generated `dist` or `node_modules` files are committed.

## Future Follow-Ups

Potential next CI tasks:

- Add formatting check.
- Add coverage reporting.
- Add integration tests for `GET /map/places`.
- Add SQL migration validation after Supabase schema exists.
- Add branch protection in GitHub settings.
- Add CD workflow after the production host is selected.

