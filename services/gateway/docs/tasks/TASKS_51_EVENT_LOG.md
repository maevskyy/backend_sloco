# TASKS 51: Event log MVP — telemetry intake, serving receipts, ML export

**Status: DONE — live in prod, full acceptance passed 2026-08-16.** Migration `022`
applied, both services deployed, export cron installed (03:15 UTC, root crontab).
Live evidence: intake `202 {accepted: 2, duplicates: 0, rejected: [bad_client_ts]}`
→ resend `{accepted: 0, duplicates: 2}`; unknown type accepted; 501 events → 429;
bad envelope → 400. Personalized feed returned `requestId
15d41258-6e0a-4a3d-b733-4283479986bb` + positions 0–9 (fallback feed: nulls, as
designed). A hand-sent impression/open/dwell/save scenario against that serving
produced the checklist's labeled training row in `impressions_labeled_2026-08-16.parquet`:
place `4485186644683441449` (Kabo), position 0, score 0.9439 with full serve-time
`score_components`, `action_types [card_dwell, card_open, save_favourite]`,
`dwell_ms 12000`, `weights_preset text_direct`, `value_weights_version v1_2026-08`.

Source spec: `x-algorithm` handoff, `sloco_event_log_backend_spec.md` (v2, with the
2026-08-16 §2.0 clarification: the rec-service PREPARES the receipt data, the gateway
WRITES it). The event format dictionary comes from the frontend half of the spec.
The rec-service half of this feature is `services/recommendation/docs/TASKS_8_serving_receipt.md`.

## What was built

1. **Migration `022_event_log.sql`** — four new tables, all RLS-enabled with no
   policies (PostgREST sees nothing; the direct pg pool writes):
   - `events_raw` — append-only telemetry facts. `event_type` is text (new client
     types need no migration), `known_type` marks dictionary membership. Indexes:
     `(user_id, server_ts)`, `(place_id, event_type)`, `(request_id)`, `(server_ts)`.
   - `identity_links` — `(anon_id, user_id)` pairs; readers join pre-login history
     through it, old rows are never updated.
   - `rec_served` + `rec_served_items` — one serving "receipt" per recommendation
     snapshot: final 0-based positions and the FULL `score_components` captured at
     serve time. `request_id` has no FK anywhere by design (events may arrive first).
2. **`POST /v1/events`** — new `events` module (controller → service → store):
   - Optional auth: `user_id` comes from the token ONLY; anonymous batches carry
     `anon_id`. A present-but-invalid token → 401.
   - Per-event validation: `event_id` (uuid) / `event_type` / parseable `client_ts`
     decide acceptance; a bad event lands in `rejected[]` with a reason while its
     neighbours are accepted. Unknown `event_type` → stored, `known_type=false`.
   - One multi-row INSERT per batch (the whole batch as ONE jsonb parameter,
     `ON CONFLICT (event_id) DO NOTHING`) → resends cost nothing and report
     `duplicates`. Response `202 {accepted, duplicates, rejected[]}`.
   - Limits per spec: >500 events or >1 MiB body → **429** (Fastify's 413 is
     remapped on this route).
   - An authenticated batch with `anon_id` upserts `identity_links` (login stitching).
3. **Feed threading** — `feed.requestId` (uuid | null) + per-card `position`
   (0-based, nullable) in `GET /v1/feed/places`:
   - One `request_id` per recommendation SNAPSHOT: all pages, re-sorts and category
     cuts of the same cached snapshot share it (the app's impression dedupe per
     (serving, place) relies on this). `position` = the card's index in the LOGGED
     snapshot — stable under `sort=distance` and `category=`, while `rank` stays
     positional per page.
   - On every fresh snapshot (cache miss or `debug` bypass) the gateway
     asynchronously writes `rec_served` + all `rec_served_items` — after the
     response, never on its critical path; one retry, then the receipt is dropped
     with a log line (spec: losing single receipts is acceptable). Cache hits do
     not rewrite. `config_overrides` records `{"debug": true}` for bypass servings,
     otherwise `{}` (prod runs zero overrides — TASKS_7 parity).
   - Fallback feeds are NOT servings: `requestId: null`, `position: null`, no
     receipt row. Their events arrive with `request_id null` — an expected,
     monitored share per the spec's non-MVP table.
4. **Action "price list"** — `src/config/event-value-weights.json` (`v1_2026-08`,
   numbers agreed with the research hardcodes) + zod-validated loader. Weights are
   never written into events; the version is stamped into every `rec_served` row.
   Changing a weight = a one-line PR to the JSON.
5. **Nightly ML export** — `services/recommendation/scripts/export_event_log.py`
   (ships inside the rec-service image, which has pandas+pyarrow; psycopg added).
   Three parquet files per UTC day: raw events, served items, and
   `impressions_labeled` (one row per SEEN item: serve-time `score_components` +
   aggregated follow-up actions + `dwell_ms`; actions may label an impression across
   the day boundary). Idempotent per day.

## Contract docs

- `FRONTEND_EVENTS_API.md` — the iOS contract for `POST /v1/events` and the
  requestId/position echo rules.
- `FRONTEND_FEED_API.md` — updated with `feed.requestId` + card `position`.

## Deviations from the spec SQL (all additive, called out in migration 022)

- `events_raw.context jsonb` column: the frontend envelope's `context` carries
  `mode`/`city`/`profile_id` beyond the extracted `request_id`/`position` columns;
  dropping them would lose facts.
- `events_raw.id` is a PRIMARY KEY (spec listed a bare bigserial).
- A malformed (non-uuid) `context.request_id` on an otherwise valid event stores as
  NULL rather than rejecting the event — the spec pins exactly three reject reasons.

## Verification (all run locally)

- Gateway: `pnpm build` / `pnpm typecheck` / `pnpm lint` / `pnpm test` — **200 tests
  green** (10 new events-route tests: anonymous intake, token user_id, identity
  linking, 401, resend→duplicates, unknown type, partial accept with reasons,
  429 over 500 events, 429 over 1 MiB, 400 envelope; 6 new feed-service tests:
  one receipt per fresh snapshot / none on hit, snapshot positions under
  sort=distance, feed survives a double write failure, single-failure retry,
  older-rec-service skew → requestId null, fallback → no receipt).
- SQL against a real Postgres engine (pglite, migration 022 applied verbatim):
  the events-store insert (intra-batch duplicate skipped, resend = 0 rows), the
  identity upsert (distinct pairs), the rec-served CTE insert (retry is a no-op,
  null profile/components round-trip), and all three export queries (day windows,
  DISTINCT ON impressions, action aggregation incl. next-day actions, dwell_ms
  extraction) — all pass.
- Export script: imports/args verified; parquet writes verified incl. list columns
  and empty days.

## Kirill's steps to ship

1. Run migration `022_event_log.sql` in the Supabase SQL editor (pure CREATEs,
   nothing existing is touched).
2. Commit + push `dev`; deploy BOTH services (rec first or together):
   `gh workflow run deploy-production.yml --ref dev -f service=all -f ref=dev`.
   NOTE: this recommender deploy also ships TASKS_7 (photo channel) and TASKS_6
   (coverage guard) if they are not live yet.
3. Install the nightly export cron on the host (03:15 UTC example):

   ```text
   mkdir -p /opt/backend_sloco/exports
   crontab -e:
   15 3 * * * cd /opt/backend_sloco && /usr/bin/docker compose run --rm -e SUPABASE_DB_URL -v /opt/backend_sloco/exports:/exports recommendation-service python scripts/export_event_log.py --out /exports >> /var/log/sloco-event-export.log 2>&1
   ```

   `SUPABASE_DB_URL` is read from the CI-rendered `/opt/backend_sloco/.env` by
   compose itself; `-e` forwards it into the run container. The container runs as
   the non-root `app` user — make `exports/` writable for it
   (`chmod 777 /opt/backend_sloco/exports` is fine on this single-purpose host).

## Acceptance (all passed live, 2026-08-16)

- [x] `POST /v1/events` with a small batch → 202; resend → `duplicates` = batch size.
- [x] Unknown `event_type` → 202, row lands with `known_type=false`.
- [x] Authenticated `GET /v1/feed/places` → `feed.requestId` uuid + `position` on
      every card (and nulls on fallback feeds); the serving's items landed in
      `rec_served_items` (proven by the labeled join below).
- [x] `rec_served.value_weights_version = 'v1_2026-08'` on new rows.
- [x] Export produces the three parquet files, and joining a hand-made
      impression + save scenario by `request_id` produced a labeled row
      (spec checklist item) — see Status for the row.
