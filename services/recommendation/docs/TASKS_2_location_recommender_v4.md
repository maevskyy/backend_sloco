# Task 2: Location Recommender v4 Migration

## Goal

Replace the request-time algorithm behind the private endpoint

```http
POST /v1/recommendations/personalized
```

with the new `location_recommender_v4` engine (multi-profile taste clustering,
hybrid scoring, CSLS hubness correction, MMR diversity, cold-start fallback),
while keeping the request/response contract byte-compatible so the Node gateway
(`/v1/feed/places`) keeps working unchanged.

Source of the algorithm: the data team's repo `bedkir/sloco_recommendation_system`
(`recommendation_system/ai_location_recommender/`). We take only the algorithm; the
data is our own.

## Status

- Workstream B (algorithm swap) — **implemented** behind `RECOMMENDER_ALGORITHM`
  (default still `embedding_recommender_v1`). Pending local
  `poetry run pytest / ruff / mypy` verification.
- Workstream A (data cutover: new `sloco` mapper → `places` keyed by `cid`) —
  mapper **implemented** (`services/gateway/scripts/integrations/sloco/map.ts` +
  `map:sloco`); pending `pnpm build / lint` and the (destructive) `places` reimport.
- Enabling v4 in prod (ship artifacts + flip the flag) — **pending**, after A and
  verification. The destructive `places` reimport requires explicit sign-off.

## Approach

Swap in place. Keep the FastAPI skeleton (`main.py`, routers, `health/`, the
`/v1/...` paths) and the Pydantic contract (`recommendations/schemas.py`). Change
only the algorithm object and add an adapter that maps the new rich result down to
the existing `PersonalizedResponse`. The gateway is not touched.

Contract preserved (what the gateway reads — unchanged):

```text
{ user_id, algorithm_version, embedding_run_id,
  input_summary: { favourites_count, want_to_go_count, valid_input_count, invalid_place_ids, candidate_count },
  recommendations: [ { rank, place_id, score, similarity? } ] }
```

The old `embedding_recommender_v1` stays in place, selectable by env flag, for
instant rollback.

## Workstream A — Data cutover (unblocks the id mismatch)

The new catalog/embeddings are keyed on the numeric Google `cid`
(e.g. `10008423346367752387`); the live system is keyed on Google Place IDs
(`ChIJ...`) via `places.source_id`. They do not string-match. We resolve this the
established way — a new source mapper into the same standardized `places` table
(no migration):

1. Extend the canonical mapper record in
   `services/gateway/scripts/integrations/_shared/place-record.ts` from the 15
   pre-v2 columns to the full v2 `places` column set (`primary_type`,
   `google_rating`, `google_user_rating_count`, `price_min_ron`, `price_max_ron`,
   `ai_card_summary`, `ai_place_type_summary`, `ai_tags`, `ai_tags_json`,
   `ai_confidence`, `axis_*` ×8, `map_visibility_score`, `map_visibility_rank`).
   Existing tripadvisor/osm mappers keep emitting their subset (rest → NULL).
2. Add mapper `services/gateway/scripts/integrations/sloco/map.ts` + a `map:sloco`
   package script. Source: `catalog/locations_combined_food_ttd.csv` (~12,578
   rows). Mapping: `place_id` (cid) → `source_id`, `source = "sloco_ai"`; rich
   columns 1:1; `city` → `city` + derived `country`; `theme`/`theme_group`/other
   analytics → `attributes` jsonb; full row → `raw`. Output CSV → manual Supabase
   import (existing flow — `scripts/README.md`).
3. Import strategy — clean cutover vs additive (DECISION — see Open Decisions).
   WARNING: a clean cutover TRUNCATEs `public.places` and cascade-clears
   `saved_places` / `saved_collection_places` (FK on delete cascade). DESTRUCTIVE —
   acceptable pre-launch, must be confirmed explicitly.
4. Verify while writing the mapper: `ai_confidence` format (the algorithm compares
   the string `"low"`, but `places.ai_confidence` is `numeric` — may need
   conversion) and `ai_tags_json` encoding (must be a valid JSON array for `jsonb`;
   see `_shared/python-literal.ts`).

After cutover `places.source_id` = cid, matching the recommender's key.

## Workstream B — Algorithm swap (recommendation service)

1. Port 5 files from the source repo's `ai_location_recommender/` into a new
   sub-package `src/recommendation_service/algorithms/location_recommender/`:
   `backend_recommender.py`, `location_recommender_utils.py`,
   `item_to_item_rerank.py`, `common.py`, `__init__.py`. Import closure is
   self-contained; visual/direct-image/ranker/CLI files are NOT ported. Keep
   `embedding_recommender.py`.
2. Dependencies (`pyproject.toml`): add `pandas (>=2.0,<3.0)`,
   `scikit-learn (>=1.3,<2.0)`; `poetry lock`. (No pyarrow for text-only.)
3. Adapter `algorithms/location_recommender/adapter.py` — exposes the same
   `.recommend(favourites, want_to_go, limit, exclude_input_places)` signature as
   the old recommender; internally calls the new `recommend()` and maps its rich
   result down to the old payload (5 `input_summary` fields; per item
   `{rank, place_id, score, similarity}` where `similarity` comes from
   `score_components`). Keeps `service.py` and `schemas.py` unchanged.
4. Config (`config.py`): add `RECOMMENDER_ALGORITHM`
   (`embedding_recommender_v1` | `location_recommender_v4`, default the old one),
   `LOCATIONS_CSV_PATH`, `RECOMMENDER_WEIGHTS_PRESET` (default `text_only`).
5. Lifespan (`main.py`): build the selected recommender (the new one via
   `LocationRecommender.from_artifacts(..., config={"weights": TEXT_ONLY_WEIGHTS})`,
   wrapped in the adapter); store in `app.state.recommender`; register in the
   algorithm `registry`. `service.py`/router unchanged.
6. Tests: pin the old algorithm in `conftest.py` so existing tests stay green; add
   a new test module + tiny fixtures (`tiny_locations.csv` + tiny npy/metadata) for
   the new algorithm and the adapter (same response shape, cold-start fallback,
   exclusion, unknown ids, determinism, `debug=true` → similarity); add a contract
   test asserting the JSON keys equal what the gateway reads.

## Runtime Data / Artifacts

Text-only needs 3 files (~15.5 MB) in `services/recommendation/artifacts/`, baked
into the image by the existing `COPY artifacts` (no manual server push):

```text
location_embeddings_food_drink_gpt-5.4-mini.npy
location_embeddings_food_drink_gpt-5.4-mini_metadata.csv
locations_food_drink_gpt-5.4-mini.csv        # recommender's own locations input
```

(or the `combined_food_ttd` run — see Open Decisions). The ~1.5 GB handoff bundle
is NOT shipped; only these artifacts.

## Out Of Scope

- Multimodal blend (visual / direct-image embeddings, pyarrow).
- Richer payload to the app (`profile_id`, `reason_tags`, `score_components`) —
  needs synchronized `schemas.py` + gateway TS changes.
- Item-to-item endpoint (`recommend_similar`).
- Loading the recommender's locations from the DB (`from_dataframes`).
- A new Supabase migration (we reuse the v2 `places` table via a new mapper source).

## Rollout & Rollback

Ship code (flag defaults to old → deploy is neutral) → run mapper + import
(cutover) → place artifacts → verify on staging with
`RECOMMENDER_ALGORITHM=location_recommender_v4` → flip the prod flag. Rollback =
set the flag back to `embedding_recommender_v1`.

## Verification

- `poetry run pytest` (old + new green), `poetry run ruff check .`,
  `poetry run mypy src`; for the gateway mapper `pnpm build && pnpm test && pnpm lint`.
- Smoke on the new flag: `GET /v1/health/ready` ok; `POST /v1/recommendations/personalized`
  returns the old shape; cold-start 0/1/2 seeds → results; ≥3 seeds → personalization;
  input places excluded; unknown ids → `invalid_place_ids`; determinism; latency < 5 s.
- End-to-end: gateway + recommender via docker-compose, `GET /v1/feed/places` for an
  authenticated user with saved places → `personalizationStatus="personalized"`.

## Open Decisions (confirm on approval)

1. Import strategy: clean cutover (DESTRUCTIVE — wipes `saved_*`; recommended
   pre-launch) vs additive (new `source` alongside the old rows).
2. Recommender coverage: `combined_food_ttd` (matches the places catalog; food +
   things-to-do, multi-city) vs `food_drink_gpt-5.4-mini` (lighter first step;
   Bucharest food & drink only).
3. `source` value for the new mapper (default `"sloco_ai"`).
