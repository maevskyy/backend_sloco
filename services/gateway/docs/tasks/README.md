# Task Plans

This folder indexes implementation plans.

Task files live here as `TASKS_N_NAME.md`.

## Existing Tasks

| Status | File | Summary |
| --- | --- | --- |
| Done | `TASKS_1_CI.md` | CI with parallel build, test, lint jobs. |
| Done | `TASKS_2_CD.md` | Manual production deploy, originally to Lightsail. Current deploy target is Hetzner. |
| Done | `TASKS_3_DB.md` | Supabase foundation and first raw TripAdvisor table. Historical; current serving table is `places`. |
| Done | `TASKS_4_FIRST_ENDPOINT.md` | First map endpoint for frontend. Historical shape. |
| Done | `TASKS_5_LOGGING.md` | Grafana Cloud logging setup. Historical Lightsail setup. |
| Done | `TASKS_6_POLISHED_LOGS.md` | Structured backend logging. |
| Done | `TASKS_7_GRAFANA_DASHBOARD_LOGS.md` | Grafana logs dashboard. |
| Done | `TASKS_8_REPO_REFACTORING.md` | Current lightweight layered OOP module architecture. |
| Done | `TASKS_9_SWAGGER.md` | Swagger/OpenAPI contract for frontend. |
| Done | `TASKS_10_SERVER_METRICS.md` | Server and backend container metrics dashboard. Historical host setup. |
| Done | `TASKS_11_DB_PLACES.md` | Unified source-agnostic `places` table. |
| Done | `TASKS_12_INTEGRATION_MAPPERS.md` | Per-source mappers into the `places` import format. |
| Done | `TASKS_13_MAP_DENSITY_RANKING.md` | Zoom-based map density and ranking. |
| Done | `TASKS_14_MAP_BBOX_ONLY.md` | Drop required `city`, make the map endpoint bbox-only. |
| Done | `TASKS_15_SERVER_MIGRATION.md` | Minimal Hetzner deploy migration runbook. |
| Done | `TASKS_16_SUPABASE_AUTH_FOUNDATION.md` | Supabase Auth JWT validation and `/v1/me` backend foundation. |
| Done | `TASKS_17_SAVED_PLACES.md` | Saved places + collections frontend contract rework. |
| Done | `TASKS_18_LAYERED_MODULE_MIGRATION.md` | Extracted shared infra (`config/openapi.ts`, `src/http/`, error schemas) and migrated `map`/`me`/`health`/`auth` onto the layered OOP + zod-OpenAPI standard. |
| Superseded | `TASKS_19_MAP_DOT_DENSITY.md` | Increase map density with `featured` and `dot` display tiers. Superseded by `TASKS_29` (backend-owned tiers removed; frontend owns marker styling). |
| Done | `TASKS_20_DOMAIN_HTTPS_NGINX_HARDENING.md` | Production domain, HTTPS, and Nginx hardening for `sloco.pp.ua`. |
| Done | `TASKS_21_PLACE_PHOTOS_INTEGRATION.md` | Supabase Storage upload and metadata import for place photos. Storage since moved to R2 — see `TASKS_33`; `place_photos` metadata flow survives. |
| Done | `TASKS_22_PLACES_SCHEMA_V2_IMPORT.md` | Replace MVP `places` with enriched `locations.csv` schema and import flow. Live: migration `009`, `sloco_ai` catalog (12 578 places) is the serving data. |
| Done | `TASKS_23_OBSERVABILITY_LATENCY_BREAKDOWN.md` | Standard backend HTTP/dependency metric logs through Loki for latency breakdown. |
| Done | `TASKS_24_LIGHTWEIGHT_MAP_PINS_PLACE_DETAILS.md` | Slim map pin feed and separate place details endpoint. |
| Done | `TASKS_25_PLACE_SEARCH.md` | Global fuzzy place search through Postgres trigram indexes and Supabase RPC. |
| Done | `TASKS_26_DECIDE_FEED_API.md` | Gateway feed endpoint for the iOS `Decide for me` screen backed by recommendation-service, hydration, fallback, and MVP in-memory cache. |
| Done | `TASKS_27_MAP_SPATIAL_COVERAGE.md` | Spatially fair map pin selection and capped-result metadata for `/v1/map/places`. |
| Done | `TASKS_28_MAP_STABLE_PINS.md` | Stable map pin membership through zoom-based visibility-score thresholds and safety-cap metadata. |
| Done | `TASKS_29_FRONTEND_OWNED_MAP_PIN_TIERS.md` | Remove backend-owned `featured`/`dot` tiers from the map pin contract; frontend owns marker styling. |
| Done | `TASKS_30_PLACE_DETAILS_REDIS_CACHE.md` | Stage 1 Redis cache for public `GET /v1/places/:id` details with TTL and manual import flush. |
| Done | `TASKS_31_SELF_HOST_OBSERVABILITY.md` | Move observability off Grafana Cloud to self-hosted Grafana + Loki + Prometheus on the Hetzner prod host; deploy/provisioning works in production. |
| Done | `TASKS_32_MAP_VECTOR_TILES.md` | Production map via Mapbox Vector Tiles generated from PostGIS, with Redis tile cache. Live: migration `014`, `map-tile` service, iOS consumes the tiles (re-verified 2026-08-11). |
| Done | `TASKS_33_PHOTO_STORAGE.md` | Photo storage moved to Cloudflare R2 with storage-agnostic serving (`PHOTO_BASE_URL`) and the `photos:index-sloco` metadata indexer. Live: iOS serves `pub-….r2.dev` photo URLs. |
| Done | `TASKS_34_USER_REACTIONS_STORE.md` | User-reactions part 2/3: `place_reactions` table (keyed by `source_id`) + a separate `reactions` CRUD module (PUT/DELETE/GET). Live: migration `015`, iOS writes reactions (re-verified 2026-08-11). |
| Done | `TASKS_35_USER_REACTIONS_FEED_INTEGRATION.md` | User-reactions part 3/3: feed seeding + hard-exclusion (engine & fallback), reaction in the cache key, and `reaction` echo on the feed card / place detail. Live in `feed.service.ts` (re-verified 2026-08-11). |
| Done | `TASKS_36_PLACE_PHOTOS_LIST.md` | Frontend request 1/3: best-first `photos[]` (direct R2 URLs, cap 20) on `GET /v1/places/:id` for the fullscreen gallery. Gateway-only; a second indexed `place_photos` query, no migration. Commit `0c6e9ed`. |
| Done | `TASKS_37_FEED_PAGINATION.md` | Frontend request 2/3: stable `offset` pagination for `GET /v1/feed/places` — bigger cached rec snapshot (~100) hydrated in full and windowed per page, cache key decoupled from `limit`/`offset`, global `rank`. Migration `016` raises both feed RPC caps 50→200. Verified live from iOS 2026-07-31 and 2026-08-07 (`offset=2` → ranks 3–5). |
| Planned | `TASKS_38_ONBOARDING_COMPLETE.md` | Onboarding 1/5 (GW): new `onboarding` module + `POST /v1/onboarding/complete` — picks → `saved_places` (favourites), set `profiles.onboarding_status`, no cache bust. Independent, ship-now. |
| Planned | `TASKS_39_ONBOARDING_TREE.md` | Onboarding 2/5 (GW): `GET /v1/onboarding/tree?city=` — fetch artifact from rec (`TASKS_4`), resolve numeric `placeId`, rewrite `photos[]`→R2 `photoUrls[]`, cache per city. |
| Planned | `TASKS_40_ONBOARDING_SIMILAR.md` | Onboarding 3/5 (GW): `POST /v1/onboarding/similar` — thin proxy to rec `TASKS_5` for off-artifact expansion; reuses `TASKS_39` id/photo helpers. |
| Planned | `TASKS_41_MAP_TILE_DENSITY_CAP.md` | iOS ask: per-tile top-N cap in `map_tile()` via per-zoom `LIMIT` (6/10/15/25/uncapped), score floor kept only ≥z18; `MAP_TILE_VERSION` bump busts caches. One migration + one env. |
| Planned | `TASKS_42_FEED_SORT.md` | iOS ask: `sort=relevance\|distance` on `/v1/feed/places` — app-layer stable re-sort of the snapshot (RPCs already return `distance_m`), positional rank, 400 validation, `feed.sort` echo. No migration. |
| Planned | `TASKS_43_FEED_SNAPSHOT_DEPTH.md` | iOS ask: raise `FEED_SNAPSHOT_SIZE` 100→200 (RPC caps and rec-service limits already allow 200 since `TASKS_37`/migration `016`). One-line change + tests. |
| Planned | `TASKS_44_PLACE_DATA_BACKFILLS.md` | iOS ask (quick half): NULL-guarded backfills — `price_level` from the in-repo catalog CSV (23.5% coverage at source, null in prod) and `google_maps_uri` synthesized from the CID; mapper learns `google_maps_uri`. Data ops, no migration. |
| Planned | `TASKS_45_SEARCH_CATEGORY_RADIUS.md` | iOS ask: `category` (seven-bucket vocabulary in `places/common/place-buckets.ts`) + `radiusMeters` hard filter on `/v1/search/places`; browse mode with optional `q`. RPC migration + gateway schemas. |
| Planned | `TASKS_46_FEED_CATEGORY_FILTER.md` | iOS ask: `category` on `/v1/feed/places` — RPC-level filter on the fallback path, post-hydration filter on the personalized path; filter→sort→rank→slice pipeline. Depends on `TASKS_45` vocabulary. |
| In progress | `TASKS_47_PLACE_DETAILS_IMPORT.md` | iOS ask (long half): address/hours/phone/website from the raw DataForSEO scrape on Kirill's SSD — `details:dataforseo` mapper + `dumps/place_details_delta.csv` (100% of prod CIDs matched; addr ~100%, hours 64%, phone 73%). Remaining: staging import + NULL-guarded UPDATE (ops). |

## TBD Backlog

- `TBD_SELF_HOST_OBSERVABILITY.md` - superseded by `TASKS_31_SELF_HOST_OBSERVABILITY.md` (kept for rationale/decision history).
- `TBD_TRACING_LATENCY_BREAKDOWN.md` - future OpenTelemetry tracing for backend, database, and response latency breakdown.
- `TBD_PLACE_DETAILS_ENRICHMENT.md` - superseded by `TASKS_47_PLACE_DETAILS_IMPORT.md` (the fields were found in the local raw DataForSEO scrape — no external fetch needed; kept for rationale/decision history).

## Naming Rule

New task files should use:

```text
TASKS_N_SHORT_NAME.md
```

Keep task docs decision-oriented:

- what changes;
- why;
- files or areas involved;
- test plan;
- assumptions.

Do not use task docs as permanent product docs. Once a task creates lasting
behavior, link or summarize that behavior from `docs/CURRENT_STATE.md`,
`docs/DECISIONS.md`, `docs/README.md`, `README.md`, or a runbook.
