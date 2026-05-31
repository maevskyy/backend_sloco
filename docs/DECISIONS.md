# Decisions

Small current decision log. This is not a full ADR system; it is a compact map
of the choices that affect future work.

| Topic | Current Choice | Why |
| --- | --- | --- |
| Product shape | Taste-based city discovery, not Google Maps clone | Recommendations should feel personal, not exhaustive. |
| Backend runtime | Node.js, TypeScript, Fastify | Simple monolith, fast iteration, low overhead. |
| Hosting | Hetzner Ubuntu + Docker Compose + Nginx | More control than PaaS, still simple enough for MVP. |
| Database | Supabase managed Postgres | Avoid DB ops, backups, restores, security, and scaling work during MVP. |
| Serving table | `public.places` | One source-agnostic table for TripAdvisor, OSM, and future providers. |
| Geo lookup | PostGIS `geom` + `places_in_bbox` RPC | Efficient bbox queries with GiST index. |
| Map query | Bbox-only, no required `city` | The viewport is the source of truth; avoids city/bbox mismatch bugs. |
| Map density | Backend ranks and limits results | Frontend renders; backend decides what is worth showing. |
| Data imports | Offline mappers + manual Supabase import | Good enough for MVP, keeps provider quirks out of API code. |
| Auth | iOS Supabase Auth SDK + backend JWT validation | Supabase handles sign up/sign in/session; backend owns product APIs and user-owned data. |
| API contract | Swagger/OpenAPI | Frontend agents can consume generated contract. |
| Observability | Grafana Cloud + Alloy for now | Fast visibility; self-hosting remains a later cost/control decision. |
| Git flow | User commits and pushes manually | Agents edit and verify, but do not commit/push. |
