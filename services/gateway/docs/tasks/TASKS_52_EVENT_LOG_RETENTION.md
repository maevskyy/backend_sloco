# TASKS 52: Event-log retention — Postgres is the buffer, parquet is the archive

**Status: DONE — live in prod 2026-08-16.** New image (`94debdb5a052`) boots with
the same coverage lines (100% text / 91.3% direct-image); a manual run of the
exact cron command exported yesterday (0-row files — the tables were born the
same day) and finished with `cleanup: nothing older than 2026-07-17` — the
retention path executes, the 30-day window is simply still empty. First real
deletions are expected around 2026-09-15.

Kirill's call (2026-08-16, right after TASKS_51 went live): rows that are already
exported to parquet have no reason to stay in Postgres. At 1k DAU the serving
receipts alone would grow the database by 1–4 GB/day (200 items × ~1 KB
`score_components` per serving, a new serving per reaction-driven cache miss), which
is pure storage cost — nothing reads old rows at runtime.

## What was built

`--retention-days N` on `services/recommendation/scripts/export_event_log.py`
(no new script — cleanup belongs to the exporter so it can never run against an
unexported day):

1. The nightly run exports the day as before. **Only if the export succeeded** the
   cleanup step runs.
2. Cleanup finds every UTC day older than `today − N` that still holds rows
   (`events_raw` ∪ `rec_served`), and for each day, **verifies all three parquet
   files exist in `--out`**. A day with missing files is KEPT and reported
   (`cleanup: KEEPING 2026-08-20 — export files missing…`) — the failure mode is
   always "data stays", never "data lost". Consequence: move parquet files off the
   host by COPYING; if they must leave the host, do it after cleanup has passed
   that day.
3. A verified day is deleted in one transaction: `rec_served_items` (via its
   serving header — the table has no timestamp), then `rec_served`, then
   `events_raw`. Counts are logged per day.
4. `identity_links` is not exported and is NEVER touched. Without the flag the
   script behaves exactly as before (export only) — manual re-exports stay safe.
5. Floor: `--retention-days` < 2 is rejected. Offline devices flush queued events
   days late, and those events need their serving rows still present for the
   labeled join; the recommended window is **30**.

Note on disk: Postgres reuses freed pages, so the database size PLATEAUS at
roughly `N days × daily volume` rather than shrinking — that plateau is the goal.

## Cron line (replaces the TASKS_51 one)

```text
15 3 * * * cd /opt/backend_sloco && /usr/bin/docker compose run --rm -e SUPABASE_DB_URL -v /opt/backend_sloco/exports:/exports recommendation-service python scripts/export_event_log.py --out /exports --retention-days 30 >> /var/log/sloco-event-export.log 2>&1
```

## Verification

- `ruff` clean; argparse floor rejects `--retention-days 1`; without the flag the
  script is byte-identical in behavior (connection error dry-runs).
- All four cleanup SQL statements validated against a real Postgres engine
  (pglite, migration 022 applied): day discovery returns only days past the
  cutoff; the per-day delete removed exactly the old day's 2 items / 1 serving /
  1 event while the recent day and `identity_links` survived intact.
