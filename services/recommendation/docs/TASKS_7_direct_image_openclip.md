# TASKS 7: Dashboard parity for the production recommender — re-vendor + direct-image channel

**Status: Implemented locally 2026-08-12 — awaiting commit + `service=recommender`
deploy** (see "Implementation record" at the end for what was built and measured).
This spec is written to be **self-contained**: every path, code anchor and command
needed to execute it is in this file — do not rely on conversation memory.

**Goal in one line:** the production recommender must behave exactly like the research
dashboard's defaults (`/Volumes/Extreme SSD/sloco/SLOCO/recommendation_system`) — same
engine code, same default knobs, and the **direct-image (photo) channel ON** with OpenCLIP
place embeddings.

Prerequisite: the external SSD must be mounted at `/Volumes/Extreme SSD/` (artifact copy +
re-vendor source). Everything else is local.

---

## The three deltas between prod and the dashboard (all verified 2026-08-12)

1. **Engine code drifted.** The vendored engine
   (`services/recommendation/src/recommendation_service/algorithms/location_recommender/`)
   was copied ~2026-06-28; research moved to a 2026-07-02 state. Missing on the backend:
   - **`WEIGHT_GROUPS`** per-profile channel weights + `_resolve_profile_weights()` +
     config field `weight_groups_enabled: bool = True` (**ON by default** — so dashboard
     parity REQUIRES this code) + `profile_weight_group` in the debug output;
   - **cross-theme injection 2026-07-02 fixes**: `interleave_into_window()`,
     `taste_target_distribution()` / `taste_window_kl()` / `inject_focus_factor()` (the
     focus damper), new config fields `cross_theme_inject_window: int = 0`,
     `inject_focus_kl_min: float = 0.3`, `inject_focus_kl_ref: float = 1.2`, changed
     default **`cross_theme_inject_max` 0.20 → 0.10**, injection utility
     `vibe_fit + 0.15·quality`, and the duplicate-safe re-insert.
   `common.py` and `item_to_item_rerank.py` are byte-identical — no drift there.
   `algorithm_version` default is identical on both sides
   (`location_recommender_v4_more_direct`) — no contract change.
2. **The direct-image channel cannot even be enabled.** `Settings` (`config.py`) has no
   fields for the artifact paths and `adapter.py` hardcodes `None` for all of them.
3. **The weights preset is `text_only`** (deploy workflow's v4 block), while the
   dashboard default is `TEXT_DIRECT_WEIGHTS`
   (semantic 0.26 · direct_image 0.50 · tag 0.08 · axis 0.06 · quality 0.06 · price 0.04 —
   the "more_direct" gate winner). The vendored preset values are already byte-identical
   to research; only the env selects the wrong one.

**Decision on the image model:** the dashboard's literal artifact default is
`siglip2_naflex_v1`, but **Kirill explicitly chose OpenCLIP** (2026-08-12) — research's
own OpenCLIP-vs-SigLIP2 comparison ("Task B") is still undecided, and OpenCLIP is the
documented production image model in their `CURRENT_STATUS.md`. Both sets cover the same
17 936 places → **11 483 / 12 578 (91.3%) of the live catalog**. Swapping to SigLIP2 later
is the same 2-file + 2-env change (naflex set: 41 MB, 1152-d float16, same
`place_embedding_store/` folder).

**Parity definition used by this spec:** engine files re-vendored to the 2026-07-02
research state (+ our marked backend extensions re-applied) · `RECOMMENDER_WEIGHTS_PRESET=
text_direct` · zero config overrides (all other knobs = the dataclass defaults, which the
dashboard also uses) · direct-image artifacts loaded. The dashboard's UI-only layers
(collaborative sandbox, LightGBM ranker bench, visual-fit bench) are diagnostic and are
NOT part of its default scoring path — nothing to port for them.

---

## Step 1 — Re-vendor the engine (audit P1)

Source (research): `/Volumes/Extreme SSD/sloco/SLOCO/recommendation_system/ai_location_recommender/`
Target (backend): `services/recommendation/src/recommendation_service/algorithms/location_recommender/`

1. Copy `backend_recommender.py` and `location_recommender_utils.py` from research over
   the backend copies. (`common.py` / `item_to_item_rerank.py`: verify `diff` is empty,
   leave as is.) Do NOT copy `dashboard_app.py`, `artifacts_manifest.py`, or anything
   else — the module's file set stays exactly these four + `adapter.py` + `__init__.py`.
2. Re-apply the **import rewrites** (the vendoring transform):
   - `backend_recommender.py` top (~lines 13–15):
     `from recommendation_system.ai_location_recommender import location_recommender_utils as utils` →
     `from . import location_recommender_utils as utils`; same for
     `... import item_to_item_rerank as itr` → `from . import item_to_item_rerank as itr`;
     `from recommendation_system.ai_location_recommender.common import normalize_matrix` →
     `from .common import normalize_matrix`.
   - `location_recommender_utils.py` (~line 1460, inside a function):
     `from recommendation_system.ai_location_recommender.backend_recommender import (` →
     `from .backend_recommender import (`.
   - Verify no others: `grep -n "recommendation_system" <both files>` must return nothing.
3. Re-apply the **backend extension** (dislike/hide support — the gateway sends these;
   losing them breaks `feed.service.ts`). Three clusters, all marked
   `# backend extension (not in upstream)`; anchors given in research line numbers:
   - **`LocationRecommender.recommend()` signature** (~line 1169, after
     `want_to_go_place_ids`):
     ```python
     dislike_place_ids: list[str] | None = None,  # backend extension (not in upstream)
     hide_place_ids: list[str] | None = None,  # backend extension (not in upstream)
     ```
   - **Exclusion logic** (~line 1181, right after `favourites` / `want_to_go` are
     deduped and `candidate_df` exists):
     ```python
     exclude_ids = _dedupe_preserve_order(  # backend extension (not in upstream)
         (dislike_place_ids or []) + (hide_place_ids or [])
     )
     exclude_set = set(exclude_ids)  # backend extension (not in upstream)
     favourites = [
         place_id for place_id in favourites if place_id not in exclude_set
     ]  # backend extension (not in upstream)
     want_to_go = [
         place_id for place_id in want_to_go if place_id not in exclude_set
     ]  # backend extension (not in upstream)
     if exclude_set:  # backend extension (not in upstream)
         candidate_df = candidate_df[~candidate_df["place_id"].isin(exclude_set)].copy()
     ```
   - **`input_summary` counts** (~line 1213, inside the summary dict):
     ```python
     "dislike_count": len(_dedupe_preserve_order(dislike_place_ids)),
     "hide_count": len(_dedupe_preserve_order(hide_place_ids)),
     ```
   The old vendored copy also had a local `~pool["place_id"].isin(in_merged)` filter in
   the cross-theme block — **do NOT re-apply it**: research's 2026-07-02 version handles
   duplicates itself (removes picks from `base` before re-inserting).
4. Contract check afterwards: `adapter.py` imports `TEXT_ONLY_WEIGHTS`,
   `TEXT_DIRECT_WEIGHTS` from `.backend_recommender` and calls
   `LocationRecommender.from_artifacts(...)` — both still exist post-re-vendor (verified
   against the research file). `tests/test_recommendations.py` +
   `test_location_recommender_v4.py` assert response SHAPE and invariants, not exact
   rankings — they must pass unmodified. If an assertion fails on ordering only,
   investigate before touching it: WEIGHT_GROUPS is a no-op on the tiny fixtures (no
   diet-niche `primary_type`s), so ordering should not change.

## Step 2 — Ship the OpenCLIP artifacts

```bash
cp "/Volumes/Extreme SSD/sloco/SLOCO/handoff_for_backend/embeddings/direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1.npy" services/recommendation/artifacts/
cp "/Volumes/Extreme SSD/sloco/SLOCO/handoff_for_backend/embeddings/direct_image_embeddings/place_embedding_store/direct_place_image_embeddings_openclip_vitb32_v1_metadata.parquet" services/recommendation/artifacts/
```

18.4 MB `(17936, 512) float16` + 0.75 MB parquet (17 936 rows; join key `place_id` as
string = our CID; also carries `direct_place_embedding_row`,
`has_direct_image_embedding`). Committed to git like the existing artifacts (the folder
already holds 90+ MB; the Docker build bakes `artifacts/` into the image). There is **no
profiles CSV for this set — that is fine**: `_load_direct_image_artifacts()` treats
profiles as optional (missing → empty DataFrame). The loader reads parquet via
`_read_table`, which requires **pyarrow** — check `pyproject.toml` dependencies; if
pyarrow is absent, add it (poetry) — the engine cannot read the metadata without it.

## Step 3 — Wire the channel (settings → adapter → guard)

1. `src/recommendation_service/config.py` — three new optional fields on `Settings`,
   default `None` (pattern-match the existing `embeddings_npy_path` field style):
   `direct_image_embeddings_npy_path` (alias `DIRECT_IMAGE_EMBEDDINGS_NPY_PATH`),
   `direct_image_metadata_path` (alias `DIRECT_IMAGE_METADATA_PATH`),
   `direct_image_profiles_csv_path` (alias `DIRECT_IMAGE_PROFILES_CSV_PATH`).
2. `algorithms/location_recommender/adapter.py` — `build_location_recommender_v4()`
   passes the three settings through to `from_artifacts` instead of the hardcoded `None`s
   (`visual_embeddings_npy` / `visual_metadata_path` / `visual_profiles_csv` STAY `None` —
   the GPT-4V channel has weight 0 and its artifacts are deliberately not shipped).
   Add a `direct_candidate_count` property on the adapter: the number of catalog places
   with a direct-image embedding (implementation free; e.g. join
   `self._recommender.direct_image_metadata["place_id"]` against
   `locations["place_id"]`, or a `has_direct_image` column if the engine exposes one).
3. `main.py` — extend the startup block: when
   `settings.recommender_algorithm == "location_recommender_v4"` and the direct paths are
   set, log `direct-image coverage: N/M locations (X%)` next to the existing
   `log_v4_embedding_coverage` line, and **WARN when N == 0** (that means the artifact
   paths are wrong — the silent-mismatch trap, same class as audit P0-2). Do NOT apply
   the 95% text threshold here: the honest expectation is **≈ 91.3%**.

## Step 4 — Turn it on (compose + deploy workflow)

1. Root `docker-compose.yml`, `recommendation-service` environment — pass-through with
   empty defaults (channel off unless the env provides paths):
   ```yaml
   DIRECT_IMAGE_EMBEDDINGS_NPY_PATH: ${DIRECT_IMAGE_EMBEDDINGS_NPY_PATH:-}
   DIRECT_IMAGE_METADATA_PATH: ${DIRECT_IMAGE_METADATA_PATH:-}
   ```
2. `.github/workflows/deploy-production.yml`, the v4 block (the `if RECOMMENDER_ALGORITHM
   = location_recommender_v4` heredoc) — change
   `RECOMMENDER_WEIGHTS_PRESET=text_only` → **`text_direct`** and add, in the same block
   (P0-2 lesson: the flag and its artifacts move together or not at all):
   ```text
   DIRECT_IMAGE_EMBEDDINGS_NPY_PATH=artifacts/direct_place_image_embeddings_openclip_vitb32_v1.npy
   DIRECT_IMAGE_METADATA_PATH=artifacts/direct_place_image_embeddings_openclip_vitb32_v1_metadata.parquet
   ```

## Step 5 — Tests

Fixtures live in `tests/fixtures/` (`tiny_locations.csv` has `place_1`…`place_6`).
Generate the direct-image pair once (committed, like the other fixtures):

```python
import numpy as np, pandas as pd
rng = np.random.default_rng(7)
ids = [f"place_{i}" for i in range(1, 5)]          # 4 of 6 places have photos
np.save("tests/fixtures/tiny_direct_embeddings.npy",
        rng.normal(size=(len(ids), 8)).astype(np.float16))
pd.DataFrame({
    "place_key": ids, "place_id": ids,
    "direct_place_embedding_row": range(len(ids)),
    "has_direct_image_embedding": True,
    "direct_photo_embeddings_total": 3,
    "direct_image_selection_strategy": "top_quality",
    "model_tag": "openclip_vitb32", "run_id": "tiny",
}).to_parquet("tests/fixtures/tiny_direct_metadata.parquet", index=False)
```

New test file `tests/test_direct_image_channel.py`, three cases (reuse the
`test_location_recommender_v4.py` client-fixture pattern, adding the env vars):

1. **Off by default / backward compat:** direct env vars unset → service boots and
   responds exactly as today (existing tests already cover the responses; assert the
   coverage log line is absent).
2. **On:** `RECOMMENDER_WEIGHTS_PRESET=text_direct` + both paths set → boots, coverage
   line logged (4/6), personalized response valid, and the ranking DIFFERS from a
   `text_only` run with the same seeds (the photo signal contributes).
3. **Mismatch trap:** paths set but pointing at an empty/na metadata (or ids that match
   nothing) → the WARN fires (or startup fails loudly) — never a silent fall-back to
   text-only behaviour without a log.

## Step 6 — Verify and ship

1. `make check` (pytest + ruff + mypy) — note the vendored engine dirs are excluded from
   strict ruff/mypy in `pyproject.toml`, so the re-vendor does not need lint cleanup.
2. Local boot against the real artifacts (paths via env pointing into `artifacts/`):
   startup shows `v4 embedding coverage: 12578/12578 (100.0%)` AND
   `direct-image coverage: 11483/12578 (91.3%)`.
3. Commit (`dev`), push (CI runs on push), deploy `service=recommender ref=dev` — this
   deploy also finally ships the TASKS_6 coverage guard, which has been code-complete but
   undeployed.
4. Production acceptance:
   - startup log: both coverage lines, no WARN,
     `algorithm=location_recommender_v4_more_direct` (the label is unchanged — but it now
     becomes literally true: until now prod ran that label with text-only weights);
   - one authenticated feed request (the test account
     `testuser1123@mail.com` has 3 favourites) still returns
     `personalizationStatus: "personalized"`;
   - its top-5 place ids **differ** from the pre-deploy top-5 for the same account
     (capture before deploying!) — the photo signal is actually changing the ranking;
   - gateway feed latency unchanged in practice (the 10-min rec cache absorbs the extra
     ~30 ms; research measured service p95 ≈ 110 ms with this channel on).
5. Close-out per the ship ritual: audit `P1` and the OpenCLIP half of `P2` → resolved
   notes in `../../../recommender-config-audit.md`; this file's Status → DONE with the
   observed numbers; `docs/README.md` (this folder) already lists TASKS_7.

## Rollback

`RECOMMENDER_WEIGHTS_PRESET=text_direct → text_only` in the workflow block + redeploy:
`direct_image_similarity` weight becomes 0 and ranking is text-only again (the loaded
18 MB matrix is inert; removing the two path envs as well restores today's memory
footprint). The re-vendored engine stays — with `text_only` weights the 07-02 additions
are dormant (WEIGHT_GROUPS only ever swaps weights for diet-niche seed profiles; with the
old behaviour wanted exactly, `weight_groups_enabled=false` exists as a config field, but
do not pre-wire an env for it — YAGNI).

## Out of scope

- SigLIP2 (either variant) — research Task B decides the model on visual-fit ratings;
  the swap is: 2 files from the same `place_embedding_store/` + the 2 path envs.
- The GPT-4V visual-text channel (`visual_*`) — weight 0, artifacts not shipped.
- Photo-level embedding shards (`photo_embedding_store/`) — the scoring path uses
  place-level embeddings only.
- Gateway changes — none needed; the feed contract and cache are untouched
  (`debug=true` similarity passthrough remains a separate optional item).
- The dashboard's diagnostic layers (collaborative sandbox, LightGBM ranker, visual-fit
  bench) — not part of its default scoring path.

## Executor freedom

Naming of new Settings fields/properties, the exact shape of the coverage computation,
test file organization — free. NOT free: the artifact set (OpenCLIP v1 as pinned above),
the preset (`text_direct`), zero config overrides, preserving the three marked backend
extensions, and the flag+paths moving together in the deploy workflow.

---

## Implementation record (2026-08-12)

**Steps 1–5 done, locally verified. Not yet committed/deployed** — that is Kirill's step.

| Step | Result |
|---|---|
| 1 re-vendor | `backend_recommender.py` refreshed whole-file; diff vs research is now EXACTLY the 3 relative-import lines + the 3 marked backend-extension clusters (7 marker comments, same as before). `location_recommender_utils.py`: zero drift — after the one import rewrite it is byte-identical (md5 `aa00d594…`) to the previous vendored copy. `common.py` / `item_to_item_rerank.py`: md5-identical, untouched. The old local `in_merged` filter was NOT re-applied (research dedups before re-inserting). |
| 2 artifacts | `artifacts/direct_place_image_embeddings_openclip_vitb32_v1.npy` (18.4 MB, `(17936, 512)` float16) + `…_metadata.parquet` (0.75 MB, 17 936 rows, no nulls, unique `place_id`). **pyarrow was absent → added** to `pyproject.toml` (`>=18.0,<22.0`) and `poetry.lock` regenerated (only pyarrow 21.0.0 added, `lock-version` stays `2.1`, `poetry check --lock` passes). |
| 3 wiring | `config.py`: three `Path \| None` settings + a `mode="before"` validator mapping the empty string to `None` (compose passes `${VAR:-}`, and `Path("")` would otherwise resolve to `.`). `adapter.py`: pass-through + `direct_candidate_count` (sum of `has_direct_image_embedding` on the prepared frame). `main.py`: `log_v4_direct_image_coverage()`, no ratio threshold, WARN only at 0. |
| 4 turn-on | compose passthrough for the two paths; workflow v4 block now `text_direct` + both paths. |
| 5 tests | fixtures `tiny_direct_embeddings.npy` (4×8 float16) + `tiny_direct_metadata.parquet` (place_1…4); `tests/test_direct_image_channel.py` — off-by-default (no coverage line, no WARN), on (`4/6` logged, ranking differs from the same preset without artifacts), foreign-catalog metadata (`0/6` + WARN). |

**Verification actually run** (rec service): `ruff check .` clean · `mypy src` clean ·
`poetry check --lock` clean · `pytest` **26 passed** (23 pre-existing, unmodified, still
green after the re-vendor + 3 new).

Local boot on the REAL artifacts, via the actual FastAPI lifespan:

```text
v4 embedding coverage: 12578/12578 locations have embeddings (100.0%)
v4 direct-image coverage: 11483/12578 locations have photo embeddings (91.3%)
Loaded recommender: algorithm=location_recommender_v4_more_direct candidates=12578
```

Same 3 real favourites, `limit=10`, two configurations:

| run | top-10 overlap | warm request |
|---|---|---|
| `text_only`, channel off (= today's prod) | — | 921 ms |
| `text_direct` + OpenCLIP artifacts | **0 of 10 ids in common** | 915–951 ms |

So the photo signal reorders the whole list, and it costs nothing measurable at request
time (engine init +0.2 s, ~2 s cold first request in both configurations). The ~0.9 s
absolute is the existing v4 cost on a laptop, not something this change introduces.

**Deviations / decisions taken while implementing** (none change the pinned items):

1. Metadata ships as **parquet, not CSV** (and hence pyarrow) — deliberate: the engine
   merges `direct_image_embedding_metadata["place_id"]` onto a str-typed catalog column
   **without casting**, so a CSV round-trip would let pandas re-infer the numeric CID as
   int64 and join zero rows. Parquet carries the dtype. (Swapping to CSV would require
   patching vendored code — out of bounds.)
2. `tests/test_direct_image_channel.py` installs a throwaway `AlgorithmRegistry` for its
   own boots. The registry is process-wide and `/v1/meta` reads it; the new file sorts
   before `test_health.py`, so without this its v4 registration leaked into that test's
   expected `algorithms` list. Pre-existing order-dependence, contained locally.
3. `DIRECT_IMAGE_PROFILES_CSV_PATH` exists as a Setting (spec §3) but is deliberately NOT
   plumbed through compose/workflow — there is no profiles CSV for this set.
4. Doc-of-record touch-ups beyond the spec's list: `.env.example` (both vars, empty) and
   `services/recommendation/README.md` (one paragraph on the channel).

**Noticed, not touched:** the vendored engine raises a pandas `FutureWarning`
(`backend_recommender.py:1119`, downcasting on `.fillna`) — upstream code, harmless
today, will need the research side to fix it before pandas 3.

### Pre-deploy production baseline (captured 2026-08-12, before the deploy)

`GET /v1/feed/places?limit=10` as `testuser1123@mail.com` (3 favourites), on the live
`text_only` v4 — `personalizationStatus: "personalized"`:

```text
2797 Bollo - Restaurant · 707 Naan Project · 9968 Candy Bar · 7884 shandra'ma Batistei
8715 Haveli Pakistani/indian Restaurant · 8229 Simbio · 10168 Soprano
4614 MoMo Bucharest · 3394 Exile · 9554 Zepelin 1929 Resto-Bar
```

The post-deploy list must differ from this one. Mind the feed's per-user
**10-minute recommendation cache** (`CACHE_TTL_MS`, `feed.service.ts`): a request right
after the restart can still serve the pre-deploy snapshot, so compare after the TTL or
treat only a clearly different list as proof.

### Remaining (Kirill's steps)

1. `git add -A && git commit && git push origin dev` in `backend_sloco`.
2. `gh workflow run deploy-production.yml --ref dev -f service=recommender -f ref=dev`
   (this deploy also finally ships the TASKS_6 coverage guard).
3. Post-deploy acceptance per §6.4 against the baseline above, then flip this file's
   Status to DONE.
