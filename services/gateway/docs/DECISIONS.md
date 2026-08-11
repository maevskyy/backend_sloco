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
| Map tile density | Per-tile top-N cap by `mapVisibilityScore` (6/10/15/25 by zoom band, uncapped ≥z18; migration `017`), not a global per-zoom score floor; `MAP_TILE_VERSION` bumped to 2 with it | One floor cannot serve cities of different density (Tbilisi z13 235 vs Bucharest 119 over the same floor). A cap is self-balancing: dense areas thin out, sparse ones keep everything, zoom-in only ever reveals more. |
| Feed sort | `sort=relevance\|distance` applied in the gateway as a stable re-order of the ranked snapshot before the offset slice; no migration (both feed RPCs already return `distance_m`), no cache-key change | "Nearby" must reorder the recommender's own candidates, not run a different query. Sorting after the recommendation cache keeps one snapshot per user and keeps both orderings consistent page over page. |
| Feed snapshot depth | 200 places per refresh cycle (was 100) | The ceiling both feed RPCs (migration `016`) and the rec-service limit already allowed; a swipe-per-screen feed exhausts 100 in one session. |
| Place details data | Address / hours / phone / website imported from the raw DataForSEO scrape by `cid`, via a staging table + NULL-guarded UPDATE (`TASKS_47`); price from the catalog's categorical `price_level`, maps URI synthesized from the CID | The catalog pipeline dropped these fields, but the raw scrape behind it has them at 100% CID coverage — so no Google Places API spend. NULL-guarded updates keep the import idempotent and never overwrite better data. |
| Category vocabulary | Seven coarse buckets (`cafe, food, bar, culture, nature, shopping, leisure`) defined once in `places/common/place-buckets.ts`, shared by search and feed; matched WORD-BOUNDARY against the venue kind (`primary_type`/`category`) only — never the `types` array | One vocabulary means the client maps its chips once. `types` is an attribute bag (`garden` = "has a terrace"), so matching it filed restaurants under `nature`; word boundaries stop "bar" matching "barbecue restaurant". |
| Search modes | `q` and `category` are independent; either alone is valid. Text = trigram ranking; category-only = browse (nearest-first, no scoring). `radiusMeters` is a hard cut, not a boost | A chip is a browse, not a query. Proximity as a ranking boost could not stop a name match 1 500 km away from winning. |
| Backend module architecture | Lightweight layered OOP modules: `controller -> service -> store`, with `index.ts` and `<feature>.module.ts`. All product modules (`map`, `me`, `health`, `saved-places`) migrated; `auth` stays a shared service with its DB call in a store. | Easier to scale than flat Fastify files while staying simple; `saved-places` is the reference implementation. |
| Shared backend code | Split by responsibility — `lib/` adapters, `config/` wiring (incl. `openapi.ts` generator + `http-schemas.ts`), `http/` controller glue. No `shared/`/`utils/` bucket. | Avoids a dumping ground; each piece has an obvious home. |
| OpenAPI source | zod schemas per module, generated to OpenAPI 3.0 components via `config/openapi.ts` | One source of truth; runtime validation and docs cannot drift. |
| API contract | Swagger/OpenAPI | Frontend agents can consume generated contract. |
| Observability | Grafana Cloud + Alloy for now | Fast visibility; self-hosting remains a later cost/control decision. |
| Git flow | User commits and pushes manually | Agents edit and verify, but do not commit/push. |
