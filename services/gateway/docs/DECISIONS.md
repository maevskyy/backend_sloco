# Decisions

Small current decision log. This is not a full ADR system; it is a compact map
of the choices that affect future work.

| Topic | Current Choice | Why |
| --- | --- | --- |
| Product shape | Taste-based city discovery, not Google Maps clone | Recommendations should feel personal, not exhaustive. |
| Backend runtime | Node.js, TypeScript, Fastify | Simple monolith, fast iteration, low overhead. |
| Hosting | Hetzner Ubuntu + Docker Compose + Nginx | More control than PaaS, still simple enough for MVP. |
| Database | Supabase managed Postgres | Avoid DB ops, backups, restores, security, and scaling work during MVP. |
| Migrations | Add one only when Postgres itself must change (table/column/index/constraint/RPC body); otherwise change the query or app layer | Migrations are permanent, ordered, run-once; the schema should not churn for anything the application can do. See `AGENTS.md` → Migration Restraint Rule. |
| Serving table | `public.places` | One source-agnostic table for TripAdvisor, OSM, and future providers. |
| Geo lookup | PostGIS `geom` + `places_in_bbox` RPC | Efficient bbox queries with GiST index. |
| Map query | Bbox-only, no required `city` | The viewport is the source of truth; avoids city/bbox mismatch bugs. |
| Map density | Backend ranks and limits results | Frontend renders; backend decides what is worth showing. |
| Data imports | Offline mappers + manual Supabase import | Good enough for MVP, keeps provider quirks out of API code. |
| Auth | iOS Supabase Auth SDK + backend JWT validation | Supabase handles sign up/sign in/session; backend owns product APIs and user-owned data. |
| Saved places | `public.saved_places`, `public.saved_collections`, and `public.saved_collection_places` via backend-only APIs | Keeps user-owned data private, supports the iOS Saved tab, and turns saved/collection intent into future recommendation signal. |
| User reactions | `public.place_reactions` keyed by stable `places.source_id`, with the public API still using bigint `placeId` | Reactions survive catalog reimports while the frontend keeps the existing id model. |
| Place photos in details | `GET /v1/places/:placeId` returns both `primaryPhoto` and bounded `photos[]`, hydrated by a second indexed `place_photos` query on cache miss | One details fetch/cached object is simpler than a separate gallery endpoint or RPC rewrite. |
| Feed pagination | `GET /v1/feed/places` takes `offset`; a bigger cached rec snapshot (~100) is hydrated in full and windowed per page, cache key excludes `limit`/`offset`; migration `016` raises both feed RPC caps 50→200 | Deep, stable page-over-page ranking; the RPC-body cap was the blocker, so a migration is the right tool (both personalized and fallback page deep). |
| Backend module architecture | Lightweight layered OOP modules: `controller -> service -> store`, with `index.ts` and `<feature>.module.ts`. All product modules (`map`, `me`, `health`, `saved-places`) migrated; `auth` stays a shared service with its DB call in a store. | Easier to scale than flat Fastify files while staying simple; `saved-places` is the reference implementation. |
| Shared backend code | Split by responsibility — `lib/` adapters, `config/` wiring (incl. `openapi.ts` generator + `http-schemas.ts`), `http/` controller glue. No `shared/`/`utils/` bucket. | Avoids a dumping ground; each piece has an obvious home. |
| OpenAPI source | zod schemas per module, generated to OpenAPI 3.0 components via `config/openapi.ts` | One source of truth; runtime validation and docs cannot drift. |
| API contract | Swagger/OpenAPI | Frontend agents can consume generated contract. |
| Observability | Grafana Cloud + Alloy for now | Fast visibility; self-hosting remains a later cost/control decision. |
| Git flow | User commits and pushes manually | Agents edit and verify, but do not commit/push. |
