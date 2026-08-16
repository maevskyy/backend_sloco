# TASKS 8: Serving receipt in the recommendation response

**Status: Done in code — deploys with the next `service=recommender` deploy.**

The rec-service half of the event-log feature (gateway half + tables + intake:
`services/gateway/docs/tasks/TASKS_51_EVENT_LOG.md`; source spec:
`x-algorithm` `sloco_event_log_backend_spec.md` §2.0–2.1). Division of labor per the
spec's 2026-08-16 clarification: this service PREPARES the serving receipt in its
response; the gateway persists it. **No database access is added here** — the
service keeps its DB-free canon.

## Response changes (`POST /v1/recommendations/personalized`)

All additive; the gateway tolerates their absence, so deploy order does not matter.

- Top level: `request_id` (uuid4 minted per request — the serving id),
  `weights_preset` (active blend preset; null on the legacy algorithm),
  `fallback_used` (cold-start fallback flag).
- `input_summary.profiles_count` — taste profiles built for this serving.
- Per item: `position` (0-based = rank − 1), `profile_id` (which taste profile
  produced the item; null on legacy), and `score_components` — the FULL breakdown
  as scored, **always present, not only in debug mode** (the flat `similarity`
  field keeps its old debug-only behavior for backward compatibility).

Implementation: `recommendations/service.py` (request_id mint + field mapping),
`recommendations/schemas.py` (pydantic contract), v4 `adapter.py` (passes
profile_id / score_components / fallback_used / profiles_count through from the
rich engine result), `embedding_recommender.py` (legacy fills the same payload
shape with profile_id=None and a single-component breakdown).

## Verification

`ruff` / `mypy --strict` clean; **28 pytest green**, including two new contract
tests: the v4 receipt contract (unique uuid per call, `position == rank − 1`,
components present without debug, `weights_preset` echoes the setting) and the
legacy-algorithm equivalent. The determinism test now excludes `request_id`
(minted per request by design).
