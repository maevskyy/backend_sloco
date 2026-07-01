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

Manual smoke:

```bash
poetry run uvicorn recommendation_service.main:app --port 8000
curl -s localhost:8000/v1/recommendations/personalized \
  -H 'content-type: application/json' \
  -d '{"favourites_place_ids":["ChIJ_xaqNQMCskAR8aQ8oHos1Ro"],"limit":5}'
```
