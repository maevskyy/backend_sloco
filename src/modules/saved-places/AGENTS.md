# AGENTS.md — saved-places module

Local guide for the `saved-places` module. This module is the current reference
implementation of the repo's **lightweight layered OOP** architecture. Use this
shape when migrating other product modules away from the old flat Fastify
module style.

## Purpose

Authenticated "saved places" feature: saving/un-saving places, collections,
collection membership and ordering, and the saved dashboard.

API surface (mounted under the `/v1` prefix):

```http
GET    /v1/me/saved
GET    /v1/me/saved/collections/:collectionId
POST   /v1/me/saved/places
DELETE /v1/me/saved/places/:placeId
POST   /v1/me/saved/collections
PATCH  /v1/me/saved/collections/:collectionId
DELETE /v1/me/saved/collections/:collectionId
POST   /v1/me/saved/collections/:collectionId/places
DELETE /v1/me/saved/collections/:collectionId/places/:placeId
PATCH  /v1/me/saved/collections/:collectionId/places/order
```

## Layout

```text
saved-places/
  index.ts                  public barrel (module + service + types + errors + OpenAPI components)
  saved-places.module.ts    composition root: builds the controller, wires deps
  controllers/              HTTP layer
  services/                 business logic / orchestration
  stores/                   data access (Supabase) — the only layer that talks to the DB
  common/                   types, errors, mappers, schemas, openapi
  tests/                    service + controller tests
```

## Layers and the dependency rule

Dependencies point **inward**: `controller → service → store`. Inner layers
never import outer ones.

- **controllers/** — Fastify only. Auth, `zod.parse()` of request input, mapping
  domain errors → HTTP status codes, response logging. No business logic, no SQL.
  Each handler runs through `withUser` (built on the shared `createAuthGuard` from
  `src/http/`) and `handleError` is the single place errors become HTTP responses.
  Shared glue lives in `src/http/` (`docsRoute`, `handleCommonError`,
  `createAuthGuard`, `logResponseSummary`).
- **services/** — business logic and orchestration. Pure of HTTP (`Fastify*`) and
  of SQL/Supabase. Talks to the store through the `SavedPlacesStoreContract`
  interface, never the concrete class.
- **stores/** — the only layer that touches Supabase. No business rules; it returns
  rows / mapped summaries. Raw rows are shaped by `common/saved-places.mappers.ts`.
- **common/** — shared, dependency-free building blocks (see below).

## Dependency injection & contracts

- Inject collaborators through the **constructor** (`new SavedPlacesController(service, authService)`,
  `new SavedPlacesServiceImpl(store)`). No singletons reached for inside a class.
- Public seams are **interfaces** in `common/saved-places.types.ts`:
  `SavedPlacesServiceContract`, `SavedPlacesStoreContract`. Depend on the contract,
  not the implementation.
- Each layer ships a `create…()` factory plus a default singleton
  (`savedPlacesService`). `saved-places.module.ts` is the composition root and the
  only place defaults are wired; tests pass fakes instead.

## Errors

- Domain errors are classes in `common/saved-places.errors.ts`
  (`PlaceNotFoundError`, `SavedCollectionNotFoundError`,
  `DefaultSavedCollectionDeleteError`, `CollectionPlacesOrderError`).
- Services throw them; they carry no HTTP meaning.
- The **only** place they map to status codes is `controller.handleError`: it
  checks the saved-places domain errors (not-found → 404, default-delete → 409,
  bad order → 400), then delegates the rest to the shared `handleCommonError`
  from `src/http/` (`ZodError` → 400, else 500).

## Validation & OpenAPI — zod is the single source of truth

- All request and response shapes are declared **once** as zod schemas in
  `common/saved-places.schemas.ts`, and registered in `savedPlacesSchemaRegistry`
  with a stable `id`. That `id` is the OpenAPI component name / Fastify `$id` and
  is part of the published contract — **do not rename ids casually.**
- Prefer TS DTO types in `common/saved-places.types.ts` to be `z.infer<>` of
  response/request schemas when practical. DB-row types and the `*Contract`
  interfaces stay hand-written because they are not the HTTP contract.
- `common/saved-places.openapi.ts` **generates** the JSON-Schema components from the
  registry via the shared `buildComponentSchemas` (`config/openapi.ts`) and builds
  route schemas with `makeDefineRoute` (shared `tags`/`security`/error responses,
  reusing `sharedErrorResponses` from `config/http-schemas.ts`).
  **Never hand-write JSON Schema here.**
- `config/swagger.ts` registers the components by looping over
  `savedPlacesComponentSchemas`.
- Gotcha: the generator targets `openapi-3.0`, so use `.min(1)` (not `.positive()`)
  for positive integers — `.positive()` emits a boolean `exclusiveMinimum` that
  Fastify's serializer rejects.

## Tests

- Live in `tests/`. Unit-test the service with a fake `SavedPlacesStoreContract`;
  test the controller by building the app and injecting a fake service / auth
  (no module mocking). Controller tests assert serialized response shapes, so they
  catch schema/serialization regressions.

## Adding an endpoint (checklist)

1. Add request/response zod schemas in `common/saved-places.schemas.ts`; register
   each component in `savedPlacesSchemaRegistry` with a new stable `id`.
2. Infer any new DTO type in `common/saved-places.types.ts`.
3. Add a route schema via `defineRoute` in `common/saved-places.openapi.ts`.
4. Add the method to `SavedPlacesServiceContract` + implement in the service.
5. Add any new data access to `SavedPlacesStoreContract` + implement in the store.
6. Register the route in `SavedPlacesController.register` and add a handler
   (wrap with `withUser`, parse input with the zod schema).
7. Add service + controller tests in `tests/`.
8. Run `pnpm build && pnpm test && pnpm lint`.
