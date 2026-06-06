# recommendation_service

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

The recommender is embeddings-only. It does not call OpenAI, Supabase, or any
database at request time. It loads these artifacts at startup:

```text
artifacts/location_embeddings_20260531T173837Z.npy
artifacts/location_embeddings_20260531T173837Z_metadata.csv
```

If no valid input place has an embedding, recommendations are empty because this
thin recommender has no cold-start signal.

Manual smoke:

```bash
poetry run uvicorn recommendation_service.main:app --port 8000
curl -s localhost:8000/v1/recommendations/personalized \
  -H 'content-type: application/json' \
  -d '{"favourites_place_ids":["ChIJ_xaqNQMCskAR8aQ8oHos1Ro"],"limit":5}'
```
