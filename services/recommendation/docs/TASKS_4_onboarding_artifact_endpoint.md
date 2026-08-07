# Task 4: Onboarding artifact endpoint

**Status: Planned (awaiting approval).**

Rec-service half **1 of 2** of the onboarding feature (data-team handoff
`2026-08-01`). Serves the precomputed per-city onboarding tree as a static file so
the gateway (`gateway TASKS_39`) can cache and post-process it. **No engine, no
auth, no dependency** on Task 5.

## Context

The data team shipped `artifact/` in the `2026-08-01` handoff (checksums verified):

```
onboarding_Bucharest.json   48 roots, 1008 nodes
onboarding_Tbilisi.json     48 roots, 1008 nodes
manifest.json               schema + recommender contract + data build
```

Each city file is `schema_version: 1`, immutable per `source_version`
(`locations_food_drink_multicity.csv`). Serving it is a plain file read — the build
script (`recommendation_system.ai_location_recommender.*`) runs offline on the
research machine and is **not** part of this repo (confirmed; the package doesn't
exist here, and it isn't needed).

Today the service has **no raw-file serving** (every route returns a Pydantic model
via `response_model`) and **no artifacts *directory* config** — only per-file paths
in `config.py`. Routers register under `/v1` in `main.py` (`include_router(...,
prefix="/v1")`).

## Decisions

- **Commit the two JSONs into the image** at `artifacts/onboarding/` (1.1 MB total).
  Prod already bakes artifacts into the image (no host mounts), so this keeps the
  data versioned by image tag — consistent, reproducible, no host dependency. The
  stale copies in `/opt/sloco-data/onboarding_artifacts/` (2026-06-28, non-portable
  paths) are **not** used.
- **New `onboarding` router package** (`src/recommendation_service/onboarding/`),
  registered `include_router(onboarding_router, prefix="/v1")` in `main.py` —
  mirrors the existing `recommendations`/`health` package layout.
- **Serve verbatim via `FileResponse`/`JSONResponse`**; resolve `city` through a
  **whitelist map** (`{"Bucharest": "onboarding_Bucharest.json", ...}`), never
  string-interpolate `city` into a path (path-traversal guard).
- **`source_version`** is already inside each artifact; return it as-is. A
  `/v1/meta` `source_version` field is **optional/deferred** (gateway `TASKS_39`
  doesn't require it; see blocker response §5).

## Changes (`services/recommendation`)

1. **Commit** `artifacts/onboarding/onboarding_Bucharest.json` and
   `onboarding_Tbilisi.json` from the handoff bundle.
2. **`config.py`** — add `ONBOARDING_ARTIFACTS_DIR` (default `artifacts/onboarding`).
3. **New `src/recommendation_service/onboarding/router.py`** —
   `GET /v1/onboarding/artifact?city=<city>`: whitelist-resolve the filename, read
   the JSON from `ONBOARDING_ARTIFACTS_DIR`, return it verbatim; `404` for unknown
   city. Register in `main.py`.
4. **Tests** — known city returns the file with 48 roots / 1008 nodes; unknown city
   / traversal attempt → `404`; response is byte-faithful JSON.

## Test Plan

```bash
poetry run pytest && poetry run ruff check .
```

- `GET /v1/onboarding/artifact?city=Bucharest` → 200, valid artifact JSON, roots=48.
- `?city=Tbilisi` → 200. `?city=Nope` and `?city=../secret` → `404`.
- The committed files match the handoff checksums.

## Dependencies

- **Upstream:** none (self-contained; data shipped).
- **Downstream:** gateway `TASKS_39` fetches this endpoint.

## Out Of Scope

The engine `similar` endpoint + direct-image store (Task 5); mounting
`/opt/sloco-data`; regenerating artifacts (offline research job); the `source_version`
startup drift check (optional).
