# Backend Handoff: AI Location Recommender

This document is for the backend developer and for any Codex/LLM agent that needs to integrate, review, or extend the recommendation algorithm.

The recommender is already implemented as a backend-ready Python reference module. It uses precomputed embeddings and does **not** call OpenAI during user requests.

## What This Solves

The app lets a user save places into two lists:

- `favourites`
- `want_to_go`

Given these saved places, the algorithm recommends other locations that are semantically similar to the user's taste profile.

The important design point is that user taste is not collapsed into one average vector. If the user likes different kinds of places, the algorithm clusters the saved places into multiple taste profiles and recommends for each profile.

Example:

- profile 0: quiet coworking cafes
- profile 1: lively cocktail bars
- profile 2: cozy coffee shops

## Files

Main implementation:

- `recommendation_system/ai_location_recommender/backend_recommender.py`

CLI wrapper:

- `recommendation_system/ai_location_recommender/recommend_user_cli.py`

Detailed API/spec reference:

- `recommendation_system/ai_location_recommender/backend_recommender_spec.md`

Notebook used to create and inspect embeddings:

- `recommendation_system/ai_location_recommender/location_embedding_recommender.ipynb`

Utility functions used by the notebook:

- `recommendation_system/ai_location_recommender/location_recommender_utils.py`

## Runtime Data And Artifacts

Production should use the backend database `locations` table as the source of location metadata.

The handoff archive intentionally does **not** need `locations.csv` for production if the backend already has the same data in the database.

The recommender still needs these embedding artifacts:

```text
recommendation_system/ai_location_recommender/data/embedding_store/location_embeddings_20260531T173837Z.npy
recommendation_system/ai_location_recommender/data/embedding_store/location_embeddings_20260531T173837Z_metadata.csv
```

`locations.csv` is only a local development/reference export. It is useful for smoke tests, but the production backend should read `locations` from the DB.

Current verified artifact state:

```text
locations rows: 2508
embedding matrix shape: (2508, 1536)
metadata rows: 2508
default candidate pool: 2022
embedding_run_id: 20260531T173837Z
```

Matching is done by `place_id`.

### Required `locations` DB Fields

The backend developer must provide a `locations` dataset/table with one row per place and the same `place_id` values used by the embedding metadata.

Required fields for recommendation scoring:

```text
place_id
name
ai_tags_json
ai_tags_csv
ai_confidence
map_visibility_score
google_rating
google_user_rating_count
axis_quiet_lively
axis_work_social
axis_day_night
axis_casual_premium
axis_drinks_food
axis_local_tourist
axis_cheap_expensive
axis_traditional_experimental
```

Recommended fields for debugging/admin responses:

```text
ai_place_type_summary
ai_card_summary
primary_type
price_level
price_min_ron
price_max_ron
apify_review_count
apify_rating_avg
```

The implementation validates:

- no duplicate `place_id`
- every embedding metadata `place_id` exists in the locations dataset
- every embedded row has a valid `embedding_row`

`location_embeddings_20260531T173837Z_metadata.csv` contains:

```text
source_row_index
place_id
custom_id
embedding_row
has_embedding
embedding_text_hash
error
usage_total_tokens
```

### Should Embeddings Go Into The Database?

Recommended production setup for v1:

- keep `location_embeddings_20260531T173837Z.npy` as a versioned artifact loaded into memory on service startup
- import `location_embeddings_20260531T173837Z_metadata.csv` into a small DB table, or load it as an artifact
- join DB `locations.place_id` to embedding metadata `place_id`

Suggested DB table if importing metadata:

```sql
CREATE TABLE location_embedding_metadata (
    place_id TEXT PRIMARY KEY,
    embedding_run_id TEXT NOT NULL,
    embedding_row INTEGER NOT NULL,
    has_embedding BOOLEAN NOT NULL,
    embedding_text_hash TEXT,
    usage_total_tokens INTEGER
);
```

For this dataset size, storing the actual 1536-dimensional embeddings in the DB is optional. The `.npy` file is only about 15 MB and is fast to load in memory.

If the backend already uses Postgres with `pgvector`, Qdrant, Pinecone, Weaviate, or another vector store, then it is also valid to import embeddings into that system. In that case:

- store vector by `place_id`
- keep `embedding_run_id`
- keep `embedding_text_hash`
- preserve the same filtering/scoring metadata from `locations`
- still keep a reproducible artifact backup of the `.npy` and metadata CSV

`embedding_row` points into the `.npy` matrix.

## Important Runtime Rule

Do not call OpenAI in the backend request path.

Embeddings are already generated offline. The backend only:

1. loads DB `locations`
2. loads the `.npy` embedding matrix
3. loads embedding metadata from CSV or DB
4. joins by `place_id`
5. normalizes embeddings once at startup
6. computes recommendations in memory

If location data changes materially, regenerate embeddings offline in the notebook or batch pipeline, then deploy a new artifact version.

## Python Usage

### Production: DB-backed locations

Read the backend `locations` table into a DataFrame or equivalent structure, then initialize from that data:

```python
from recommendation_system.ai_location_recommender import LocationRecommender

locations_df = load_locations_from_database()

recommender = LocationRecommender.from_dataframes(
    locations=locations_df,
    embeddings_npy="artifacts/location_embeddings_20260531T173837Z.npy",
    metadata_csv="artifacts/location_embeddings_20260531T173837Z_metadata.csv",
)
```

### Local Development: CSV-backed locations

If using local exported CSV files:

```python
from recommendation_system.ai_location_recommender import LocationRecommender

recommender = LocationRecommender.from_artifacts()

result = recommender.recommend(
    user_id="user_123",
    favourites_place_ids=[
        "ChIJ_xaqNQMCskAR8aQ8oHos1Ro",
        "ChIJAQAckz__sUARPQnYMKtxIRQ",
    ],
    want_to_go_place_ids=[
        "ChIJbYXdJwD_sUAR2yjbAGOKJPM",
    ],
    limit=100,
    exclude_input_places=True,
    debug=False,
)
```

For production, instantiate `LocationRecommender` once on service startup and reuse it for all requests.

Do not instantiate it per request, because loading DB data and the `.npy` matrix repeatedly is wasteful.

## CLI Usage

Cold-start example:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli \
  --limit 5 \
  --debug
```

Personalized example:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli \
  --favourites "ChIJ_xaqNQMCskAR8aQ8oHos1Ro,ChIJAQAckz__sUARPQnYMKtxIRQ,ChIJbYXdJwD_sUAR2yjbAGOKJPM" \
  --want-to-go "ChIJrRz_N5j_sUAReHHbh0ZcBlw" \
  --limit 10 \
  --debug
```

Useful flags:

```text
--locations-csv
--embeddings-npy
--metadata-csv
--user-id
--favourites
--want-to-go
--limit
--debug
--min-map-visibility-score
--include-low-confidence
```

## Recommended Backend Endpoint

```http
POST /recommendations/personalized
```

Request body:

```json
{
  "user_id": "user_123",
  "favourites_place_ids": ["place_id_1", "place_id_2"],
  "want_to_go_place_ids": ["place_id_3", "place_id_4"],
  "limit": 100,
  "exclude_input_places": true,
    "debug": false
}
```

Optional tuning params:

```json
{
  "min_map_visibility_score": 20,
  "exclude_low_confidence": true,
  "max_profile_clusters": 4,
  "min_saved_for_personalization": 3,
  "favorites_weight": 1.0,
  "want_to_go_weight": 0.55,
  "weights": {
    "semantic_similarity": 0.72,
    "tag_overlap": 0.10,
    "axis_similarity": 0.08,
    "quality_score": 0.07,
    "price_match": 0.03
  }
}
```

Response shape:

```json
{
  "user_id": "user_123",
  "algorithm_version": "location_recommender_v1",
  "embedding_run_id": "20260531T173837Z",
  "fallback_used": false,
  "input_summary": {
    "favourites_count": 12,
    "want_to_go_count": 8,
    "valid_input_count": 20,
    "invalid_place_ids": [],
    "profiles_count": 3,
    "candidate_count": 2022
  },
  "profiles": [
    {
      "profile_id": 0,
      "profile_weight": 9.1,
      "seed_place_ids": ["ChIJ..."],
      "top_tags": ["specialty_coffee", "cozy", "work_friendly"]
    }
  ],
  "recommendations": [
    {
      "rank": 1,
      "place_id": "ChIJ...",
      "profile_id": 0,
      "score": 0.842,
      "score_components": {
        "similarity": 0.781,
        "semantic_similarity_norm": 0.891,
        "tag_overlap": 0.42,
        "axis_similarity": 0.88,
        "quality_score": 0.79,
        "price_match": 0.91
      },
      "reason_tags": ["cozy", "specialty_coffee", "work_friendly"]
    }
  ]
}
```

If `debug=true`, each recommendation also includes display metadata:

```text
name
ai_place_type_summary
ai_card_summary
ai_tags_csv
google_rating
google_user_rating_count
map_visibility_score
ai_confidence
```

Production can return only `place_id`, `rank`, `score`, `profile_id`, `reason_tags`, and score components, then hydrate location details from the backend DB.

## Algorithm Details

### Candidate Pool

Default candidates:

- must have an embedding
- must not have `ai_confidence == "low"`
- must have `map_visibility_score >= 20`
- must not be in the user's input places when `exclude_input_places=true`

These defaults are intentionally conservative. They avoid recommending low-quality or weakly understood locations.

### User Signal Weighting

The two saved lists are weighted differently:

```text
favourites weight: 1.0
want_to_go weight: 0.55
```

Reason:

- `favourites` means the user already likes the place
- `want_to_go` is still useful, but it is weaker because the user has not confirmed liking it yet

If a `place_id` appears in both lists, the stronger favourite weight is used.

### Taste Profiles

If the user has at least `min_saved_for_personalization` valid saved places, default `3`, the algorithm:

1. takes embeddings for saved places
2. clusters saved places with cosine agglomerative clustering
3. chooses up to `max_profile_clusters`, default `4`
4. computes a weighted centroid for each taste profile
5. scores all candidates against every profile
6. merges and deduplicates recommendations globally

This prevents the common failure mode where all user preferences are averaged into one vague vector.

### Hybrid Scoring

Final score:

```text
final_score =
    semantic_similarity_norm * 0.72
  + tag_overlap              * 0.10
  + axis_similarity          * 0.08
  + quality_score            * 0.07
  + price_match              * 0.03
```

Component meanings:

- `similarity`: cosine similarity between candidate embedding and user profile centroid
- `semantic_similarity_norm`: `(similarity + 1) / 2`
- `tag_overlap`: Jaccard overlap between candidate tags and profile tags
- `axis_similarity`: `1 - mean_abs_axis_difference / 100` over AI axes
- `quality_score`: rating, review count, map visibility, AI confidence
- `price_match`: closeness on `axis_cheap_expensive`

Current default weights are defined in `backend_recommender.py` as `DEFAULT_WEIGHTS`.

### Fallback Mode

If valid saved places are fewer than `min_saved_for_personalization`, the response sets:

```json
"fallback_used": true
```

Fallback behavior:

- `0` valid saved places: rank by `quality_score`
- `1-2` valid saved places: rank by:

```text
quality_score * 0.85 + semantic_similarity_norm * 0.15
```

This prevents overfitting to one or two saved places while still using the weak signal.

## Config Defaults

Defined in `RecommenderConfig`:

```python
algorithm_version = "location_recommender_v1"
embedding_run_id = "20260531T173837Z"
favorites_weight = 1.0
want_to_go_weight = 0.55
min_saved_for_personalization = 3
max_profile_clusters = 4
min_profile_silhouette = 0.03
min_map_visibility_score = 20.0
exclude_low_confidence = True
fallback_quality_weight = 0.85
fallback_similarity_weight = 0.15
weights = {
    "semantic_similarity": 0.72,
    "tag_overlap": 0.10,
    "axis_similarity": 0.08,
    "quality_score": 0.07,
    "price_match": 0.03,
}
```

Per-request overrides can be passed into `recommend(...)` as keyword args.

## FastAPI Integration Sketch

```python
from fastapi import FastAPI
from pydantic import BaseModel, Field

from recommendation_system.ai_location_recommender import LocationRecommender

app = FastAPI()
recommender = LocationRecommender.from_artifacts()


class RecommendationRequest(BaseModel):
    user_id: str | None = None
    favourites_place_ids: list[str] = Field(default_factory=list)
    want_to_go_place_ids: list[str] = Field(default_factory=list)
    limit: int = 100
    exclude_input_places: bool = True
    debug: bool = False


@app.post("/recommendations/personalized")
def personalized_recommendations(payload: RecommendationRequest):
    return recommender.recommend(
        user_id=payload.user_id,
        favourites_place_ids=payload.favourites_place_ids,
        want_to_go_place_ids=payload.want_to_go_place_ids,
        limit=payload.limit,
        exclude_input_places=payload.exclude_input_places,
        debug=payload.debug,
    )
```

For production, initialize from DB instead:

```python
locations_df = load_locations_from_database()
recommender = LocationRecommender.from_dataframes(
    locations=locations_df,
    embeddings_npy="artifacts/location_embeddings_20260531T173837Z.npy",
    metadata_csv="artifacts/location_embeddings_20260531T173837Z_metadata.csv",
)
```

In production, consider returning only recommendation IDs and scores, then hydrate from DB:

```json
{
  "recommendations": [
    {
      "rank": 1,
      "place_id": "ChIJ...",
      "score": 0.842,
      "profile_id": 0,
      "reason_tags": ["cozy", "coffee"]
    }
  ]
}
```

## Validation Commands

Compile:

```bash
venv/bin/python -m py_compile \
  recommendation_system/ai_location_recommender/backend_recommender.py \
  recommendation_system/ai_location_recommender/recommend_user_cli.py
```

Load artifacts:

```bash
venv/bin/python - <<'PY'
from recommendation_system.ai_location_recommender import LocationRecommender

r = LocationRecommender.from_artifacts()
print("locations", r.locations.shape)
print("matrix", r.embedding_matrix.shape)
print("metadata", r.embedding_metadata.shape)
print("default candidates", len(r._candidate_pool(r.config)))
PY
```

Cold-start smoke test:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli \
  --limit 5 \
  --debug
```

Personalized smoke test:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli \
  --favourites "ChIJ_xaqNQMCskAR8aQ8oHos1Ro,ChIJAQAckz__sUARPQnYMKtxIRQ,ChIJbYXdJwD_sUAR2yjbAGOKJPM" \
  --want-to-go "ChIJrRz_N5j_sUAReHHbh0ZcBlw" \
  --limit 10 \
  --debug
```

Expected behavior:

- no OpenAI calls
- `fallback_used=false` for at least 3 valid saved places
- `fallback_used=true` for 0, 1, or 2 valid saved places
- input places do not appear in recommendations
- unknown `place_id`s appear in `input_summary.invalid_place_ids`
- same input returns same ranking for the same artifacts/config

## Notes For Codex / LLM Agents

When modifying this recommender:

1. Do not regenerate embeddings unless explicitly requested.
2. Do not add OpenAI calls to request-time recommendation code.
3. Keep `place_id` as the stable join key.
4. Preserve deterministic ranking by using stable sort tie-breakers.
5. Keep fallback behavior for users with too few saved places.
6. Run the validation commands above after any code change.
7. If changing scoring weights, update both `backend_recommender.py` and this handoff/spec document.
8. If changing artifact filenames, update defaults in `backend_recommender.py`.

## Current Verification

The current implementation was verified on real artifacts:

```text
locations: (2508, 70)
embedding matrix: (2508, 1536)
metadata: (2508, 8)
embedded locations: 2508
default candidate pool: 2022
```

Tested scenarios:

- request with 20 valid saved places
- request with unknown ID
- cold-start with 0 valid places
- fallback with 2 valid places
- deterministic repeat request
- CLI execution with debug output
