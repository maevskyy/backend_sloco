# TASKS 6: Activate `location_recommender_v4` in prod + startup coverage guard

**Status: In progress** — the coverage guard (change 2) is implemented and verified
2026-08-11: `log_v4_embedding_coverage` in `main.py` logs
`v4 embedding coverage: N/M locations have embeddings (X%)` at engine init and WARNs
below 95% (`tests/test_startup_coverage.py`; 23/23 pytest, ruff and strict mypy clean;
fixture boot shows `6/6 (100.0%)`).

> **Finding (2026-08-11): change 1 appears to be ALREADY DONE.** The server `.env` is
> rendered by `deploy-production.yml`, which appends the full v4 block (algorithm + all
> three embedding vars + `LOCATIONS_CSV_PATH` + weights preset, pinned together exactly as
> P0-2 demands) whenever the GitHub repo **Variable** `RECOMMENDER_ALGORITHM` equals
> `location_recommender_v4` — and that variable has been set since **2026-07-12T13:05Z**,
> with successful deploys 29 s later and again on 07-15 and 07-18. So prod has (very
> likely) been running v4 since mid-July; the iOS side could never observe it because
> anonymous requests never reach the recommender (`fallback_visibility_v1` is the
> gateway's own fallback label). Do NOT hand-edit `/opt/backend_sloco/.env` — the next
> deploy overwrites it; the variable + workflow are the source of truth.

> **Runtime confirmed (2026-08-11, Kirill, on the server):**
> `.env` carries `RECOMMENDER_ALGORITHM=location_recommender_v4` +
> `RECOMMENDER_WEIGHTS_PRESET=text_only`, and the startup log reads:
> `2026-07-15 16:59:47 INFO … Loaded recommender: algorithm=location_recommender_v4_more_direct
> candidates=12578 embedding_run_id=combined_food_ttd elapsed_ms=2756.15`.
> `candidates=12578` = the full catalog → artifact pair matched, the P0-2 trap did NOT
> fire. (The line is from the 07-15 deploy: 07-18 was gateway-only and did not restart
> this container.) Change 1 is therefore **done and verified**; audit P0-1/P0-2 recorded
> as resolved.

Remaining: (b) deploy the coverage guard with the next recommender deploy (it is a
seatbelt for future artifact swaps — today's coverage is already proven 100%); (c) the
authenticated feed verification below, then record the result here and close the iOS ask.
Cheapest end-to-end check without token juggling: favorite one place in the signed-in iOS
app, refresh the feed, then on the server
`docker compose logs backend | grep 'feed/places' | tail -5` → the response summary log
shows `personalizationStatus: "personalized"` with `authenticated: true`.

Resolves the still-open P0 items of `../../../recommender-config-audit.md` (P0-1 "verify
which algorithm prod actually runs", P0-2 "switch ALL artifact env vars together") and
answers iOS ask `frontend_new/messages-to-backend-dev/not-done/RECOMMENDER_STATUS.md`
(Q1: deployed? which version?). Umbrella plan:
`../../../ios-asks-implementation-plan.md` §1.

## Context (verified in code)

- The service is deployed (root `docker-compose.yml` runs `recommendation-service:8000`; the
  gateway defaults `RECOMMENDATION_SERVICE_URL` to it), but compose and `.env.example`
  default `RECOMMENDER_ALGORITHM=embedding_recommender_v1` — unless the **server `.env`**
  overrides it, the whole v4 port is dormant and prod personalization (once users have
  signals) runs the legacy cosine ranker.
- v4 reports `algorithm_version: "location_recommender_v4_more_direct"`
  (`algorithms/location_recommender/adapter.py:28`); the legacy engine reports
  `embedding_recommender_v1`.
- The artifact trap (audit P0-2): compose defaults point the three embedding vars at the
  **old** 2 508-place set while v4's locations default is the **new** 12 578-row catalog.
  Flipping the algorithm without the matching artifact set silently marks ~10 000 places
  `has_embedding=False` and excludes them from candidates — no error anywhere.
- Entry conditions (gateway side, for the iOS answer): authenticated + ≥1 favourite reaction
  or ≥1 saved place; dislike/hide only exclude. Zero client changes needed when v4 goes live.

## Changes

1. **Server `.env` (ops, all four together — never separately):**

   ```
   RECOMMENDER_ALGORITHM=location_recommender_v4
   EMBEDDINGS_NPY_PATH=artifacts/location_embeddings_combined_food_ttd.npy
   EMBEDDING_METADATA_PATH=artifacts/location_embeddings_combined_food_ttd_metadata.csv
   EMBEDDING_RUN_ID=combined_food_ttd
   ```

   Then `docker compose up -d recommendation-service` (or the deploy workflow).

2. **Startup coverage guard (code, this service)** — at engine init, log
   `candidate_count` (places with embeddings) next to the locations row count and **WARN when
   coverage < 95%**. This is the audit's cheap guard; porting upstream's strict
   `artifacts_manifest.py` loud-fail validator stays a follow-up.

3. Record the flip in the audit doc (P0-1/P0-2 → resolved, date + observed
   `algorithm_version`).

## Verification (the iOS spec's own criteria)

One authenticated feed request for an account with ≥1 favourite:

```
GET /v1/feed/places?limit=5&lat=44.4361&lng=26.1027   (Bearer <token with signals>)
→ feed.personalizationStatus == "personalized"
→ feed.algorithmVersion    == "location_recommender_v4_more_direct"
→ feed.embeddingRunId      == "combined_food_ttd"
→ whyRecommended varies per card (it is ai_the_move per place)
```

Service logs show coverage ≈100% (12 578/12 578), no WARN. Then paste the `feed` meta into
`RECOMMENDER_STATUS.md`, answer its Q1–Q6, and let the iOS side move the file per their
`messages-to-backend-dev` rule.

Rollback: flip `RECOMMENDER_ALGORITHM` back to `embedding_recommender_v1` (the four vars are
only dangerous in mixed state; the old algorithm ignores the new artifact vars' extra rows).

## Dependencies

- None. (Audit P1 — re-vendoring `backend_recommender.py` after the upstream push — becomes
  more relevant once v4 is live, but does not block the flip.)

## Out Of Scope

- Gateway `debug=true` passthrough of `similarity`/`candidate_count` (optional, umbrella
  plan §1.3 — small gateway change, no task file until scheduled).
- Weights preset upgrade to text+direct-image (audit P2 — blocked on ~0.5–1 GB artifacts).
- Collaborative engines (see `../../../user-reactions-spec.md` follow-ups).
