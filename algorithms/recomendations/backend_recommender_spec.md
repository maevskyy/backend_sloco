# Backend Personalized Location Recommender

This recommender is designed for backend runtime use. It does **not** call OpenAI during user requests. It loads precomputed location embeddings and location metadata into memory, then ranks candidate locations from a user's saved places.

## Runtime Data And Artifacts

Production should use the backend database `locations` table as the source of location metadata.

The recommender still needs these embedding artifacts:

- `recommendation_system/ai_location_recommender/data/embedding_store/location_embeddings_20260531T173837Z.npy`
- `recommendation_system/ai_location_recommender/data/embedding_store/location_embeddings_20260531T173837Z_metadata.csv`

`locations.csv` is only a local development/reference export. If the backend already has the same data in its `locations` table, do not use the CSV in production.

The embedding matrix currently has shape `(2508, 1536)`. Matching is done with `place_id`; `embedding_row` from the metadata points into the `.npy` matrix.

Required DB fields:

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

Recommended debug/display fields:

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

Recommended v1 storage:

- keep the `.npy` embedding matrix as a versioned artifact loaded at service startup
- load embedding metadata from CSV or import it into DB
- join `locations.place_id` to embedding metadata `place_id`

Optional DB table for metadata:

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

Storing full vectors in DB is optional. For this dataset, the `.npy` file is small enough to keep as an artifact. If using `pgvector` or another vector DB, store vectors by `place_id` and keep `embedding_run_id`.

## Runtime Flow

Load once at service startup from DB-backed locations:

```python
from recommendation_system.ai_location_recommender.backend_recommender import LocationRecommender

locations_df = load_locations_from_database()

recommender = LocationRecommender.from_dataframes(
    locations=locations_df,
    embeddings_npy="artifacts/location_embeddings_20260531T173837Z.npy",
    metadata_csv="artifacts/location_embeddings_20260531T173837Z_metadata.csv",
)
```

Local CSV-backed development:

```python
from recommendation_system.ai_location_recommender.backend_recommender import LocationRecommender

recommender = LocationRecommender.from_artifacts(
    locations_csv="locations.csv",
    embeddings_npy="location_embeddings_20260531T173837Z.npy",
    metadata_csv="location_embeddings_20260531T173837Z_metadata.csv",
)
```

For each request:

```python
result = recommender.recommend(
    user_id="user_123",
    favourites_place_ids=["ChIJ..."],
    want_to_go_place_ids=["ChIJ..."],
    limit=100,
    exclude_input_places=True,
    debug=False,
)
```

Recommended endpoint:

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

Optional tuning params can be passed through to `recommend`:

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

If `debug=true`, each recommendation also includes display metadata such as name, place type, card summary, tags, rating, and map visibility score. Production API can return only `place_id + scores` and hydrate full location data from the backend database.

## Candidate Rules

Default candidate pool:

- location has an embedding
- `ai_confidence != "low"`
- `map_visibility_score >= 20`
- all input places from `favourites` and `want_to_go` are excluded from recommendations when `exclude_input_places=true`

Unknown or non-embedded input IDs are returned in `input_summary.invalid_place_ids`.

## User Signal

Saved lists are weighted:

- `favourites`: `1.0`
- `want_to_go`: `0.55`

If a place appears in both lists, the stronger favourite weight is used.

If the user has at least `min_saved_for_personalization` valid saved places, saved places are clustered into taste profiles with cosine agglomerative clustering. Each profile gets a weighted centroid. Recommendations are scored against each profile, merged, deduplicated by `place_id`, and globally ranked by final score.

## Scoring

Hybrid score:

```text
final_score =
    semantic_similarity_norm * 0.72
  + tag_overlap              * 0.10
  + axis_similarity          * 0.08
  + quality_score            * 0.07
  + price_match              * 0.03
```

Components:

- `similarity`: cosine similarity between candidate embedding and taste-profile centroid
- `semantic_similarity_norm`: `(similarity + 1) / 2`
- `tag_overlap`: Jaccard overlap between candidate tags and profile seed tags
- `axis_similarity`: `1 - mean_abs_axis_difference / 100` across AI axes
- `quality_score`: map visibility, rating, review volume, and AI confidence
- `price_match`: similarity on `axis_cheap_expensive`

## Fallback Behavior

If valid saved places are below `min_saved_for_personalization`, `fallback_used=true`.

- `0` valid saved places: rank by `quality_score`
- `1-2` valid saved places: rank by `quality_score * 0.85 + semantic_similarity_norm * 0.15`

This keeps cold-start output useful while avoiding overfitting to one weak signal.

## CLI Smoke Test

Run with no saved places:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli --limit 5 --debug
```

Run with explicit saved places:

```bash
venv/bin/python -m recommendation_system.ai_location_recommender.recommend_user_cli \
  --favourites "ChIJ_xaqNQMCskAR8aQ8oHos1Ro,ChIJAQAckz__sUARPQnYMKtxIRQ" \
  --want-to-go "ChIJbYXdJwD_sUAR2yjbAGOKJPM" \
  --limit 10 \
  --debug
```

## Validation Checklist

- matrix rows = metadata rows = `2508`
- no duplicate `place_id` in locations or metadata
- every metadata `place_id` exists in DB `locations`
- requests exclude input places from output
- unknown IDs are listed in `invalid_place_ids`
- fallback requests still return recommendations
- same artifacts/config/input produce deterministic ranking
