# Task 5: Direct-image channel + `POST /v1/recommendations/similar`

**Status: Planned (awaiting approval).**

Rec-service half **2 of 2** of the onboarding feature. Two coupled pieces: (1) load
the **direct-image embedding store** so `TEXT_DIRECT_WEIGHTS` has data to score
against, and (2) the `POST /v1/recommendations/similar` endpoint that powers live,
off-artifact onboarding expansion (gateway `TASKS_40`) and general content-similar.

**Independent of Task 4.** Requires `RECOMMENDER_ALGORITHM=location_recommender_v4`
(already live in prod).

## Context

Verified on the server (2026-08-01):
- The engine's `LocationRecommender.recommend(**params)`
  (`backend_recommender.py:1054`) **accepts every param** the similar call needs
  (they flow through `config.with_overrides` and unknown keys are dropped).
- 🐞 **`min_map_visibility_score` is inert without `apply_map_visibility_filter=True`**
  — the flag defaults `False` (`backend_recommender.py:297`), the filter only runs
  when it's `True` (`~:1257`/`:1354`). The data team hit exactly this bug in their
  builder; the similar call must pass **both**.
- The current **adapter cannot forward** `weights`/`city_filter`/
  `min_saved_for_personalization` — `LocationRecommenderV4Adapter.recommend`
  (`adapter.py:49-57`) has a fixed narrow signature and freezes weights at build
  time. So similar needs **direct access to the underlying `LocationRecommender`**
  (`adapter._recommender`, `adapter.py:39`), not the adapter/service path
  (`service.py:24`).
- `build_location_recommender_v4` **hardcodes** `direct_image_*=None`
  (`adapter.py:88-108`), and `Settings` has no direct-image fields → the direct-image
  channel is **off**. `TEXT_DIRECT_WEIGHTS` puts `direct_image_similarity = 0.50`
  (`backend_recommender.py:68-76`); with no store loaded that weight is silently
  redistributed — wrong results, no error.
- The store IS on the box: `/opt/sloco-data/direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1.npy`
  (18 MB) + `_metadata.parquet` (734 KB), also shipped in the handoff `direct_image/`.
  Must be **openclip_vitb32**, not siglip2 (different embedding space). Coverage:
  11 483 of the prod catalog; all 1008 Bucharest + 1001/1008 Tbilisi nodes covered;
  the rest handled by `missing_direct_image_policy="redistribute"`.

## Decisions

- **Bake the 18 MB store into the rec image** (`artifacts/`), consistent with how
  prod already ships artifacts (no host mounts). *Alternative if preferred: a
  read-only `/opt/sloco-data/direct_image_embeddings/... :ro` volume mount on the
  `recommendation-service` block in `docker-compose.yml` (which today has no
  `volumes:`).* **← the one open decision on this task.**
- **Keep the global preset `text_only`.** The similar endpoint passes
  `weights=TEXT_DIRECT_WEIGHTS` **per call** — flipping global
  `RECOMMENDER_WEIGHTS_PRESET=text_direct` changes the **whole feed** and is a
  separate, deliberate change (out of scope).
- **Fail loud.** If the direct-image store is configured but fails to load, raise at
  startup — silent redistribution is the costly failure mode.
- **Direct engine access** for similar (bypass the adapter), reusing the loaded
  `LocationRecommender` on `app.state`.

## Changes (`services/recommendation`)

1. **`config.py`** — add `DIRECT_IMAGE_EMBEDDINGS_NPY_PATH`,
   `DIRECT_IMAGE_METADATA_PATH` (defaults pointing at the baked
   `artifacts/direct_place_image_embeddings_openclip_vitb32_v1.*`).
2. **`adapter.py` `build_location_recommender_v4`** — pass the two paths (and
   `direct_image_profiles_csv` if applicable) through instead of `None`; raise on
   load failure. Expose the raw `LocationRecommender` for direct calls.
3. **Commit / provide the store** — `artifacts/direct_place_image_embeddings_openclip_vitb32_v1.npy`
   + `_metadata.parquet` from the handoff (or wire the mount per the decision above).
4. **`POST /v1/recommendations/similar`** (`recommendations/router.py` + a
   `service.py` fn) — request `{ seed_place_id, city, k, exclude_place_ids }`,
   response `{ items: [ { place_id, score } ] }`. Calls, on the raw engine:
   ```python
   recommender.recommend(
       favourites_place_ids=[seed_place_id], want_to_go_place_ids=[],
       limit=k + buffer, exclude_input_places=True,
       weights=dict(TEXT_DIRECT_WEIGHTS), city_filter=city,
       min_saved_for_personalization=1, exclude_low_confidence=True,
       min_map_visibility_score=20.0, apply_map_visibility_filter=True,   # BOTH
       missing_visual_policy="redistribute", missing_direct_image_policy="redistribute",
   )
   ```
   then drop `exclude_place_ids`, take top-k.
5. **Tests** — store loads at startup (and raises when missing); similar returns
   ranked items; the visibility filter is actually applied (no `<20` leaks).

## Test Plan

```bash
poetry run pytest && poetry run ruff check .
```

- Startup loads the openclip store; a missing/broken store **raises** (not silent).
- `similar` for a seed returns `k` ranked `{place_id, score}`, excludes honored, no
  place below the visibility floor.
- **Parity (DoD):** 10 Bucharest roots → `similar` → high overlap with each node's
  baked `children` (not exact; near-zero ⇒ store didn't load).

## Dependencies

- **Upstream:** `RECOMMENDER_ALGORITHM=v4` (live) + the openclip store (baked or
  mounted per the decision).
- **Downstream:** gateway `TASKS_40` proxies this endpoint.

## Out Of Scope

The artifact-serving endpoint (Task 4); flipping the **global**
`RECOMMENDER_WEIGHTS_PRESET` to `text_direct` (separate change — affects the main
feed); siglip2 embeddings; the `source_version` drift check.
