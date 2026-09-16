# Recommendation service — config & port audit (2026-07-15)

Findings from comparing this repo's recommendation service against the SLOCO research
system it was ported from. Separate from `user-reactions-spec.md` (the reactions
feature): everything here is configuration checks and re-sync actions, most of them
quick. Ordered by priority.

## P0-1. Verify which algorithm prod actually runs (5 min)

> **RESOLVED 2026-08-11.** Prod runs v4. The deploy workflow renders
> `/opt/backend_sloco/.env` and appends the full v4 block when the GitHub repo Variable
> `RECOMMENDER_ALGORITHM=location_recommender_v4` is set — it has been since
> 2026-07-12T13:05Z (deploys 07-12/15/18 green). Verified on the server: `.env` carries
> the flag, startup log reads `algorithm=location_recommender_v4_more_direct
> candidates=12578 embedding_run_id=combined_food_ttd` (2026-07-15 boot).

`docker-compose.yml` and `services/recommendation/.env.example` both default to the
OLD engine:

```
RECOMMENDER_ALGORITHM: ${RECOMMENDER_ALGORITHM:-embedding_recommender_v1}
```

Unless the real server `.env` overrides this to `location_recommender_v4`, the entire
v4 port (profile clustering, CSLS, MMR, quality prior v2) is dormant and prod serves
the legacy single-embedding cosine ranker.

**Action:** check the server `.env`. If v4 is intended in prod, set
`RECOMMENDER_ALGORITHM=location_recommender_v4` — together with P0-2 below, never
separately.

## P0-2. When enabling v4, switch ALL artifact env vars to the combined run

> **RESOLVED 2026-08-11.** The workflow pins the four vars together (plus
> `LOCATIONS_CSV_PATH` and the weights preset) in one appended block, so the pair cannot
> drift; runtime shows `candidates=12578` = 100% coverage of the combined catalog — the
> silent-exclusion trap did not fire. The recommended guard now exists in code
> (`main.py` `log_v4_embedding_coverage`, WARN < 95% — see `services/recommendation/docs/
> TASKS_6_v4_activation_coverage_guard.md`); it ships with the next recommender deploy.

The Docker image bakes TWO artifact sets (`services/recommendation/artifacts/`):

| set | places | embedding coverage |
|---|---|---|
| `location_embeddings_20260531T173837Z.*` (old, food-only Bucharest) | 2 508 | 2 508 |
| `location_embeddings_combined_food_ttd.*` (current, food + things-to-do) | 12 578 | 12 578 (100%) |

The compose defaults point `EMBEDDINGS_NPY_PATH` / `EMBEDDING_METADATA_PATH` /
`EMBEDDING_RUN_ID` at the **old** set, while the v4 locations default
(`locations_csv_path` in `src/recommendation_service/config.py`) is the **new**
12 578-row `locations_combined_food_ttd.csv`.

If v4 is enabled without updating the three embedding vars, the engine joins the new
catalog against the old 2 508-row metadata: every unmatched place gets
`has_embedding=False` and is **silently excluded from candidates** — ~10 000
things-to-do places become unrecommendable, with no error anywhere.

**Action:** enable v4 only with the matching set:

```
RECOMMENDER_ALGORITHM=location_recommender_v4
EMBEDDINGS_NPY_PATH=artifacts/location_embeddings_combined_food_ttd.npy
EMBEDDING_METADATA_PATH=artifacts/location_embeddings_combined_food_ttd_metadata.csv
EMBEDDING_RUN_ID=combined_food_ttd
```

**Recommended guard (small):** at startup, log `candidate_count` (places with
embeddings) next to the locations row count, and WARN if coverage < 95%. Upstream has
a stricter loud-fail validator (`artifacts_manifest.py`, not ported) — porting it is
the fuller fix, the log+warn is the cheap one.

## P1. Vendored engine lags upstream — re-vendor after the next upstream push

> **RESOLVED 2026-08-12.** Re-vendored whole-file from the research working tree
> (`recommendation_system/ai_location_recommender/`, 2026-07-02 state) — every row of
> the table below is now present: `WEIGHT_GROUPS` + `weight_groups_enabled=True`,
> `interleave_into_window()` + `cross_theme_inject_window`, the KL focus damper and
> `cross_theme_inject_max=0.10`. `location_recommender_utils.py` turned out to carry
> ZERO drift (byte-identical after the import rewrite); `common.py` /
> `item_to_item_rerank.py` were already identical. The only local deltas are the
> relative-import rewrite and the dislike/hide backend extension, every line of it
> marked `# backend extension (not in upstream)`. See
> `services/recommendation/docs/TASKS_7_direct_image_openclip.md`.

`src/recommendation_service/algorithms/location_recommender/backend_recommender.py`
(2 676 lines) matches the upstream git repo at commit `fb3c8c9` (2026-07-02), except
it also dropped `cross_theme_inject_window` + `interleave_into_window()`. Meanwhile
the upstream working tree has moved further (2 810 lines). Missing here:

| missing in this repo | effect today |
|---|---|
| `WEIGHT_GROUPS` + `weight_groups_enabled` (per-profile weight overrides, e.g. diet-niche profiles) | real scoring difference for niche-taste users once v4 is live |
| `inject_focus_kl_min` / `inject_focus_kl_ref` (cross-theme KL focus damper) | dormant (cross-theme is off and not reachable via API) |
| `cross_theme_inject_max` default `0.10` (this repo: `0.20`) | dormant |
| `cross_theme_inject_window` + `interleave_into_window()` (dropped during vendoring — present even in `fb3c8c9`) | dormant |

**Action (two owners):** upstream pushes the pending work to the deploy repo
(Kirill's side); then re-vendor `backend_recommender.py` in one piece here. The only
intentional local delta is the relative-import rewrite (`from . import ...`) at the
top of the file — re-apply it and nothing else. Don't cherry-pick individual
features; a whole-file refresh keeps the diff auditable.

## P2. Known quality gap (informational — blocked on artifacts, no action now)

> **RESOLVED 2026-08-12.** The artifacts turned out to be far smaller than the
> "0.5–1 GB class" guess: the place-level OpenCLIP ViT-B/32 set is 18 MB
> (`(17936, 512)` float16) + 0.75 MB of parquet metadata, both committed under
> `services/recommendation/artifacts/` and covering 11 483/12 578 = 91.3% of the live
> catalog. The channel is wired (`DIRECT_IMAGE_*` settings → adapter → startup
> coverage log) and the deploy workflow now pins `RECOMMENDER_WEIGHTS_PRESET=
> text_direct` together with the two paths. Photo-level shards and the GPT-4V
> `visual_*` channel remain out of scope. See
> `services/recommendation/docs/TASKS_7_direct_image_openclip.md`.

This deploy runs the `text_only` weights preset (semantic 0.72). The upstream
production default is text+direct-image (`semantic 0.26 / direct_image 0.50`), which
measurably improves human-taste metrics (pairwise 0.521 → 0.570 in upstream A/Bs).
Closing this requires shipping direct-image embedding artifacts (~0.5–1 GB class) and
wiring the `text_direct` preset — a separate decision, listed here so it isn't
rediscovered later.

## Verified as correctly ported (no action)

- CSLS hubness correction (`hubness_method="csls"`, k=10), MMR diversity
  (`mmr_lambda=0.7`), percentile calibration, quality prior v2, missing-modality
  `redistribute`, cold-start fallback, cross-theme denylist (casino/adult).
- `item_to_item_rerank.py` present (the `/similar` endpoint is intentionally not
  exposed — the gateway doesn't call it).
- Weight preset VALUES match upstream byte-for-byte (`TEXT_DIRECT_WEIGHTS`:
  0.26 / 0.50 / 0.08 / 0.06 / 0.06 / 0.04).
- Collaborative engines (user-kNN / facet-kNN / MF / BPR) were never part of the port
  scope — see `user-reactions-spec.md` follow-ups for the plan.
