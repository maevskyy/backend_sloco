# TASKS 9: Swagger / OpenAPI Contract

## Summary

Add Swagger/OpenAPI to the backend so frontend developers and frontend agents can
use the API contract instead of asking backend questions manually.

The goal is not just a pretty Swagger UI. The important artifact is a stable
machine-readable OpenAPI JSON endpoint that Claude Code or another frontend agent
can read and use as the source of truth.

## Product Goal

Frontend should be able to understand:

- which endpoints exist;
- which query/body parameters are required;
- what response shape to expect;
- which errors can happen;
- which base URL to call in production and local development.

After this task, the backend should expose versioned API routes:

```text
GET /v1/health
GET /v1/health/supabase
GET /v1/map/places
```

And versioned Swagger routes:

```text
GET /v1/swagger/docs
GET /v1/swagger/openapi.json
```

`/v1/swagger/docs` is for humans.

`/v1/swagger/openapi.json` is for frontend codegen, Claude Code, documentation
sync, and contract checks.

## Key Decisions

### 1. Use Fastify Swagger tooling

Add:

```text
@fastify/swagger
@fastify/swagger-ui
```

Reason:

- native Fastify integration;
- supports route-level schemas;
- can expose both Swagger UI and raw OpenAPI JSON;
- no need to maintain a hand-written `openapi.yaml`.

### 2. Do not make a hand-written OpenAPI file

Avoid:

```text
docs/openapi.yaml
```

as the primary source of truth.

Reason:

- it will drift from real Fastify routes;
- it creates another place to update every time an endpoint changes;
- frontend agents need something close to real backend behavior.

Generated OpenAPI should come from route schemas registered in code.

### 3. Keep Zod runtime validation for now

Current route validation is already based on Zod:

```text
src/modules/map/map.schemas.ts
```

Do not replace this aggressively during this task.

For MVP, we can add explicit OpenAPI JSON schemas near each route and keep Zod as
runtime validation. This creates small duplication, but only for a few endpoints.

Later, if duplication starts hurting, we can introduce a Zod-to-OpenAPI bridge.
Do not add that extra abstraction until the API surface grows.

### 4. Swagger must be frontend-agent friendly

OpenAPI JSON should include:

- clear endpoint summaries;
- descriptions written for frontend usage;
- parameter descriptions;
- example requests;
- example responses;
- stable schemas/components;
- production and local server URLs;
- clear notes about current MVP limitations.

The frontend agent should be able to read `/v1/swagger/openapi.json` and
generate Swift models or client code without needing Slack/chat context.

### 5. Add `/v1` API prefix in this task

Move current public endpoints under `/v1`.

Old routes:

```text
GET /health
GET /health/supabase
GET /map/places
```

New routes:

```text
GET /v1/health
GET /v1/health/supabase
GET /v1/map/places
```

Reason:

- avoids unversioned API becoming permanent by accident;
- gives frontend a stable namespace;
- lets us add `/v2` or breaking changes later without inventing migration rules
  under pressure.

For this MVP, do not keep old unversioned aliases unless deployment healthcheck
migration requires a short temporary bridge. Prefer a clean break while only the
small team depends on the API.

## Files To Add

```text
src/config/swagger.ts
```

Purpose:

- configure `@fastify/swagger`;
- configure `@fastify/swagger-ui`;
- define OpenAPI metadata;
- define public docs route prefix.

Suggested exported function:

```ts
registerSwagger(app)
```

or:

```ts
registerSwaggerDocs(app)
```

Keep naming simple.

## Files To Update

```text
src/app.ts
```

Register Swagger before feature routes so route schemas are collected.

Register current API modules with:

```ts
await app.register(registerHealthRoutes, { prefix: "/v1" });
await app.register(registerMapRoutes, { prefix: "/v1" });
```

Exact code can differ if shared route options are needed, but all public API
routes should live under `/v1`.

```text
src/modules/health/health.routes.ts
src/modules/map/map.routes.ts
```

Add route schemas:

- tags;
- summary;
- description;
- querystring schema where needed;
- response schemas;
- example responses.

```text
package.json
pnpm-lock.yaml
```

Add dependencies.

```text
docs/FRONTEND_MAP_API.md
docs/README.md
README.md
```

Update docs to point frontend developers to:

```text
http://52.18.13.69/v1/swagger/docs
http://52.18.13.69/v1/swagger/openapi.json
```

If production exposure is not ready yet, document local URLs first:

```text
http://127.0.0.1:3000/v1/swagger/docs
http://127.0.0.1:3000/v1/swagger/openapi.json
```

## OpenAPI Info

Use:

```text
title: Sloco Backend API
version: 0.1.0
description: Taste-based city discovery backend API.
```

Servers:

```text
http://127.0.0.1:3000
http://52.18.13.69
```

Tags:

```text
Health
Map
```

Future tags:

```text
Places
Onboarding
Saves
Recommendations
Auth
```

## Endpoint Contracts

### GET /v1/health

Tag:

```text
Health
```

Success:

```json
{
  "status": "ok"
}
```

### GET /v1/health/supabase

Tag:

```text
Health
```

Success:

```json
{
  "status": "ok"
}
```

Error:

```json
{
  "status": "error"
}
```

### GET /v1/map/places

Tag:

```text
Map
```

Summary:

```text
Get places visible in a map bounding box.
```

Description:

```text
Used by the iOS map screen when the user opens the map or changes the visible
region. The frontend sends the current map viewport as south-west and north-east
coordinates. Backend returns lightweight place markers.
```

Query parameters:

| Name | Type | Required | Rules | Description |
| --- | --- | --- | --- | --- |
| `city` | string | yes | non-empty | City name. MVP supports `Berlin`. |
| `swLat` | number | yes | finite | South-west latitude. |
| `swLng` | number | yes | finite | South-west longitude. |
| `neLat` | number | yes | finite | North-east latitude. |
| `neLng` | number | yes | finite | North-east longitude. |
| `limit` | integer | no | `1..200`, default `100` | Max places returned. |

Success response:

```json
{
  "places": [
    {
      "id": 1,
      "source": "tripadvisor",
      "sourceId": "d5529357",
      "name": "Pane e Vino",
      "city": "Berlin",
      "latitude": 52.552578,
      "longitude": 13.352883,
      "rating": 4,
      "priceRange": "$$ - $$$",
      "numberOfReviews": 17,
      "rawCuisineStyle": null
    }
  ]
}
```

400 response:

```json
{
  "status": "error",
  "message": "Invalid map places query",
  "issues": []
}
```

500 response:

```json
{
  "status": "error"
}
```

## Schema Naming

Use stable component names:

```text
HealthStatusResponse
ErrorResponse
ValidationErrorResponse
MapPlacesQuery
MapPlace
MapPlacesResponse
```

Reason:

- frontend agents can refer to schemas by name;
- future generated clients become easier to diff;
- schema names stay stable even if implementation files move.

## Route Schema Style

Keep schemas close to modules.

Recommended structure:

```text
src/modules/map/map.openapi.ts
src/modules/health/health.openapi.ts
```

or, if files stay tiny:

```text
src/modules/map/map.routes.ts
src/modules/health/health.routes.ts
```

Preference:

- if route file becomes noisy, extract `*.openapi.ts`;
- do not create one giant global `schemas.ts`.

## Visibility Decision

For MVP, expose Swagger publicly.

Reason:

- no auth yet;
- endpoints are already public;
- it helps the frontend team move faster.

Public URLs after deploy:

```text
http://52.18.13.69/v1/swagger/docs
http://52.18.13.69/v1/swagger/openapi.json
```

Later, when auth/user data appears, revisit:

- hide docs in production;
- protect docs behind basic auth;
- expose only staging docs;
- keep `/v1/swagger/openapi.json` available in CI artifact instead of public
  runtime.

## CI/CD Impact

Changing `/health` to `/v1/health` affects deploy healthchecks.

Update:

```text
.github/workflows/deploy-production.yml
```

Current checks should move from:

```text
/health
```

to:

```text
/v1/health
```

This is required because deploy currently verifies the app after Docker Compose
starts. If the workflow keeps checking `/health`, deploy will fail even when the
app is healthy.

## Grafana / Alloy Impact

Alloy should not break.

Reason:

- Alloy collects Docker stdout/stderr;
- it does not know or care about application routes;
- labels like `service="backend"`, `container=...`, and `env=production` stay
  the same.

What does change:

- logs will contain `path="/v1/health"` instead of `path="/health"`;
- logs will contain `path="/v1/map/places"` instead of `path="/map/places"`;
- Grafana dashboard panels that filter by exact path must be updated.

Update Loki queries in:

```text
grafana/dashboards/backend-logs.json
docs/DEPLOYMENT.md
grafana/README.md
docs/tasks/TASKS_7_GRAFANA_DASHBOARD_LOGS.md
```

Examples:

```logql
{service="backend"} | json | path = "/v1/map/places"
```

```logql
{service="backend"} | json | path =~ "/v1/health.*"
```

Generic panels like this keep working:

```logql
{service="backend"} | json | path != ""
```

## Frontend Agent Instructions

After implementation, create a short frontend handoff note:

```text
Use /v1/swagger/openapi.json as the source of truth for backend API shape.
Do not hardcode models from chat messages if OpenAPI differs.
When endpoint behavior changes, backend updates OpenAPI first.
```

Recommended frontend prompt:

```text
Read http://52.18.13.69/v1/swagger/openapi.json and generate/update the Swift
API client for the Map and Health endpoints. Use the OpenAPI schemas as the
source of truth.
```

## Test Plan

Automated:

```bash
pnpm build
pnpm test
pnpm lint
```

Add tests:

- `GET /v1/swagger/docs` returns `200` or redirects to Swagger UI page;
- `GET /v1/swagger/openapi.json` returns `200`;
- OpenAPI JSON contains:
  - `openapi`;
  - `info.title`;
  - `/v1/health`;
  - `/v1/health/supabase`;
  - `/v1/map/places`;
  - `MapPlace`;
  - `MapPlacesResponse`.
- `GET /health` returns `404` unless we intentionally keep a temporary alias.
- `GET /v1/health` returns `200`.
- `GET /v1/map/places` returns places for a valid bbox query.

Manual local checks:

```bash
pnpm dev
curl http://127.0.0.1:3000/v1/health
curl http://127.0.0.1:3000/v1/swagger/openapi.json
open http://127.0.0.1:3000/v1/swagger/docs
```

Manual production checks after deploy:

```bash
curl http://52.18.13.69/v1/health
curl http://52.18.13.69/v1/swagger/openapi.json
open http://52.18.13.69/v1/swagger/docs
```

Frontend verification:

- send `/v1/swagger/openapi.json` to frontend Claude Code;
- ask it to generate Swift models for `MapPlacesResponse`;
- confirm generated fields match current API response;
- confirm frontend can call `GET /v1/map/places`.

## Acceptance Criteria

- Swagger UI works locally at `/v1/swagger/docs`.
- OpenAPI JSON works locally at `/v1/swagger/openapi.json`.
- Swagger UI works in production after deploy.
- OpenAPI JSON works in production after deploy.
- All existing API endpoints are moved under `/v1`.
- CD healthcheck uses `/v1/health`.
- Grafana exact-path panels are updated to `/v1/...`.
- Alloy continues shipping Docker logs with the same service labels.
- Current endpoints are documented.
- Current response schemas match real API behavior.
- Frontend can use `/v1/swagger/openapi.json` without backend explanation.
- CI stays green.

## Assumptions

- No auth yet.
- Public docs are acceptable for MVP.
- API surface is tiny, so small schema duplication is acceptable.
- We keep Zod validation as runtime validation.
- We do not add generated Swift client code in this backend task.
- We do not introduce a full API versioning strategy yet.

## Future Improvements

- Add a future `/v2` only when we need breaking API changes.
- Add generated OpenAPI artifact in CI.
- Add contract tests that compare route responses against OpenAPI schemas.
- Add frontend client generation from OpenAPI.
- Add auth docs when auth exists.
- Add examples for onboarding, saves, recommendations, and place pages.
