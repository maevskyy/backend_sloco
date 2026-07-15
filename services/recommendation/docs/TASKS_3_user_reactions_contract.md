# Task 3: User Reactions — Recommendation Contract + Engine Exclusion

## Status

Planned (awaiting approval).

Part **1 of 3** of the user-reactions feature. Order: **this (REC) → gateway
`TASKS_34` (reactions store) → gateway `TASKS_35` (feed integration)**. This is the
recommendation-service half: it teaches the wire contract and the engine about
disliked/hidden places. It is **additive and safe to deploy alone** —
`PersonalizedRequest` is `extra=ignore` today, so until the gateway starts sending
the new lists (`TASKS_35`) behavior is unchanged.

## Goal

The backend has one user↔place signal today: saved places. The product needs
explicit reactions (`favorite` / `dislike` / `hide`): favorites strengthen
personalization seeds, while disliked and hidden places must be **hard-excluded
from every feed path**. Today a user cannot say "not this" — a hidden or disliked
place can be recommended forever.

This task makes the recommendation service (a) accept the two new exclusion lists
on the wire and (b) remove those places from both the seed lists and the candidate
pool **before scoring**, on both the personalized and cold-start paths, without
touching `limit`, determinism, or the "no network at request time" guarantee.

## Decisions

- **Key space is `source_id` (text)**, matching the existing rec contract — the
  service already speaks `source_id` end to end (`favourites_place_ids` /
  `want_to_go_place_ids` are `source_id` strings). No id translation here; the
  gateway owns the bigint↔source_id mapping (`TASKS_34`/`TASKS_35`).
- **Exclusion lives at a single engine choke point** so any future direct caller
  is covered, not only the gateway feed path.
- **`dislike` and `hide` are summarized as distinct counts** from day one (so the
  follow-up hide-repulsion work can differentiate them), but in this task both
  feed the same hard-exclude set.
- Legacy `embedding_recommender_v1` must **accept** the new fields without
  crashing; real exclusion there is out of scope (documented on handback).

## Changes

1. **`src/recommendation_service/recommendations/schemas.py`**
   - `PersonalizedRequest` += `dislike_place_ids: list[str]`,
     `hide_place_ids: list[str]` (both `Field(default_factory=list)`).
   - `InputSummary` += `dislike_count: int = 0`, `hide_count: int = 0` (defaults
     required — it is built as `InputSummary(**dict)`).

2. **Engine `algorithms/location_recommender/backend_recommender.py`**,
   `recommend()` (~lines 1054-1111). Keep the diff minimal and greppable; mark
   additions `# backend extension (not in upstream)`.
   - Add **explicit** keyword params `dislike_place_ids`, `hide_place_ids` (NOT
     via `**params`, which drops unknown keys).
   - `exclude_set = set(_dedupe_preserve_order((dislike or []) + (hide or [])))`.
   - Remove excluded ids from `favourites`/`want_to_go` (they leave the seeds and
     must NOT appear in `invalid_place_ids`).
   - **Unconditionally** filter the candidate frame
     (`candidate_df = candidate_df[~candidate_df[id].isin(exclude_set)]`) right
     after it is built, independent of `exclude_input_places`.
   - Emit `dislike_count`/`hide_count` in `input_summary`.
   - Do **not** touch `_candidate_pool` / `_fallback_recommend` /
     `_personalized_recommend` — they consume the already-filtered frames, so both
     branches (incl. cold-start below `min_saved_for_personalization`) are covered.

3. **Plumbing**
   - `recommendations/service.py` — pass the two lists through (switch to keyword
     args at the `recommend()` call).
   - `algorithms/location_recommender/adapter.py` — forward the lists + map the
     two new `InputSummary` counts.
   - `algorithms/base.py` `PersonalizedRecommender` Protocol — update the
     `recommend()` signature.
   - **Legacy `algorithms/embedding_recommender.py`** (no `**kwargs`) — add
     `dislike_place_ids=None, hide_place_ids=None` to the signature (else the
     unified `service.py` call `TypeError`s) and emit `dislike_count`/`hide_count`
     = 0 in its `input_summary`. No real exclusion there (call it out on handback).

4. **Docs** — `services/recommendation/README.md`: the two new request fields +
   the two `InputSummary` counts. Verify the generated OpenAPI shows them.

## Test Plan

`poetry run pytest && poetry run ruff check . && poetry run mypy src`

- Update `INPUT_SUMMARY_KEYS` in `tests/test_location_recommender_v4.py` (else the
  contract assertion fails on the new keys).
- New cases on the `place_1..6` fixtures:
  - a `dislike_place_ids` id is absent from results; same for `hide_place_ids`;
  - an id in both `favourites_place_ids` and the exclude set is absent from results
    **and** absent from `invalid_place_ids`;
  - exclusion also holds on the cold-start path (0–2 seeds);
  - `dislike_count` / `hide_count` are correct;
  - response shape is otherwise unchanged (all existing v4/embedding tests green).

## Dependencies

- **Upstream:** none. Ships independently.
- **Downstream:** gateway `TASKS_35` (feed integration) requires this contract to
  be deployed so exclusion actually takes effect on the personalized path.

## Out Of Scope

`like`; event log (`impression`/`maps_click`); hide-repulsion ("less like this");
collaborative engines; real exclusion in the legacy algorithm; any weight/config
change; re-syncing the vendored engine with upstream; flipping the
`RECOMMENDER_ALGORITHM` default.
