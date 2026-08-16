# Recommendation Service

Python HTTP service for recommendation and algorithm runtime workloads.

This service starts as infrastructure only: health endpoints, typed settings,
Docker, CI/CD, and tests. Recommendation algorithms are added later behind typed
adapters.

## Stack

- Python 3.12
- Poetry
- FastAPI
- Uvicorn
- Pydantic
- Pytest
- Ruff
- Mypy
- Docker

## Local Development

Install dependencies:

```bash
poetry install
```

Start the development server:

```bash
poetry run uvicorn recommendation_service.main:app --reload --host 0.0.0.0 --port 8000
```

## Common Commands

```bash
poetry check --lock
poetry run ruff check .
poetry run ruff format .
poetry run mypy src
poetry run pytest
```

## Environment

Create a local `.env` from:

```text
.env.example
```

Important variables:

```text
APP_ENV
HOST
PORT
LOG_LEVEL
SERVICE_NAME
```

## Health Checks

```http
GET /v1/health
GET /v1/health/ready
GET /v1/meta
```

The Gateway should call this service through the private Docker network, not
through a public internet route.

## Personalized Recommendations

```http
POST /v1/recommendations/personalized
```

Request:

```json
{
  "user_id": "user_123",
  "favourites_place_ids": ["ChIJ..."],
  "want_to_go_place_ids": ["ChIJ..."],
  "dislike_place_ids": ["ChIJ..."],
  "hide_place_ids": ["ChIJ..."],
  "limit": 50,
  "exclude_input_places": true,
  "debug": false
}
```

Two algorithms are available, selected by `RECOMMENDER_ALGORITHM`:

- `embedding_recommender_v1` (default) — legacy, embeddings-only (numpy): one
  weighted taste centroid + cosine similarity.
- `location_recommender_v4` — multi-profile hybrid engine (taste clustering,
  CSLS, MMR, cold-start fallback). Needs `pandas` + `scikit-learn` and, besides
  the embeddings, a **locations table** (`LOCATIONS_CSV_PATH`) with place
  metadata. A thin adapter maps its rich result down to the response below, so
  the API contract is identical for both. See
  `docs/TASKS_2_location_recommender_v4.md`.

Neither calls OpenAI, Supabase, or any database at request time; each loads its
artifacts once at startup, configured by `EMBEDDINGS_NPY_PATH` /
`EMBEDDING_METADATA_PATH` (+ `LOCATIONS_CSV_PATH` for v4), e.g. the committed
legacy run:

```text
artifacts/location_embeddings_20260531T173837Z.npy
artifacts/location_embeddings_20260531T173837Z_metadata.csv
```

With `embedding_recommender_v1`, if no valid input place has an embedding the
result is empty (no cold-start signal). `location_recommender_v4` instead falls
back to a quality-ranked list for cold-start users.

`location_recommender_v4` also scores a **direct-image (photo) channel** when
`DIRECT_IMAGE_EMBEDDINGS_NPY_PATH` + `DIRECT_IMAGE_METADATA_PATH` point at a place
embedding set (OpenCLIP ViT-B/32, committed under `artifacts/`). It only contributes
under `RECOMMENDER_WEIGHTS_PRESET=text_direct` (photo weight 0.50, the research
default), so the flag and the two paths always travel together — the startup log
prints `v4 direct-image coverage: N/M` and WARNs when nothing joined the catalog.
See `docs/TASKS_7_direct_image_openclip.md`.

`dislike_place_ids` and `hide_place_ids` are accepted by the wire contract for
both algorithms. In `location_recommender_v4`, both are hard-excluded from the
candidate pool before scoring and their counts are echoed in
`input_summary.dislike_count` / `input_summary.hide_count`. In the legacy
`embedding_recommender_v1`, the fields are accepted so the contract stays stable,
but real exclusion is not implemented there yet.

The response also carries a **serving receipt** (event-log spec, `docs/TASKS_8_serving_receipt.md`):
top-level `request_id` (uuid per serving), `weights_preset`, `fallback_used`,
`input_summary.profiles_count`, and per item `position` (0-based), `profile_id`
and the full `score_components` (always present — the gateway persists them into
`rec_served_items`; the flat `similarity` stays debug-only). This service still
never touches a database: the gateway does the writing.

## Event-log export script

`scripts/export_event_log.py` (ships in this image because pandas/pyarrow live
here; psycopg is a dependency ONLY for it) exports one UTC day of
`events_raw` / `rec_served(+items)` / labeled impressions to parquet. It is run
by a host cron via `docker compose run` — see
`../gateway/docs/tasks/TASKS_51_EVENT_LOG.md` for the cron line.

Manual smoke:

```bash
poetry run uvicorn recommendation_service.main:app --port 8000
curl -s localhost:8000/v1/recommendations/personalized \
  -H 'content-type: application/json' \
  -d '{"favourites_place_ids":["ChIJ_xaqNQMCskAR8aQ8oHos1Ro"],"limit":5}'
```
