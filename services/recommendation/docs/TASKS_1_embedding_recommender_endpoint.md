# Task 1: Embedding Recommender Endpoint

## Goal

Add a private HTTP endpoint for personalized place recommendations based only on
precomputed location embeddings.

```http
POST /v1/recommendations/personalized
```

The endpoint accepts Google `place_id` values from the user's saved places,
builds one weighted taste centroid, ranks all embedded locations by cosine
similarity, and returns top-N recommended `place_id` values.

## Runtime Data

The service loads these artifacts at startup:

```text
artifacts/location_embeddings_20260531T173837Z.npy
artifacts/location_embeddings_20260531T173837Z_metadata.csv
```

Environment overrides:

```text
EMBEDDINGS_NPY_PATH
EMBEDDING_METADATA_PATH
EMBEDDING_RUN_ID
RECOMMEND_DEFAULT_LIMIT
RECOMMEND_MAX_LIMIT
FAVORITES_WEIGHT
WANT_TO_GO_WEIGHT
```

## Algorithm

This is intentionally embeddings-only:

1. Load the embedding matrix with NumPy.
2. Load metadata with stdlib `csv`.
3. Normalize every embedding row once at startup.
4. Resolve input `place_id` values to embedding rows.
5. Weight favourites as `1.0` and want-to-go as `0.55`.
6. Average seed vectors into a taste centroid and normalize it.
7. Score all candidate rows by cosine similarity.
8. Return deterministic top-N sorted by score descending and `place_id`
   ascending.

If no valid seed place has an embedding, the response is empty. A pure embedding
recommender has no meaningful cold-start signal.

## Out Of Scope

- OpenAI calls at runtime.
- Training or regenerating embeddings.
- Supabase, Postgres, or any database access.
- `locations.csv` or location attribute hydration.
- Pandas, scikit-learn, clustering, tags, axes, ratings, visibility, quality
  scoring, prices, or reason tags.
- Gateway integration and numeric `places.id` to Google `source_id` mapping.

## Verification

- Unit and integration tests use a tiny NumPy fixture.
- Manual smoke tests use the real copied artifacts.
- `make check` must pass before deploying.

