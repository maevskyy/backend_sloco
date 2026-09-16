"""Nightly parquet export of the event log (event-log spec Part 4).

Reads one UTC day from Postgres and writes three files:

  events_YYYY-MM-DD.parquet               raw telemetry facts (events_raw)
  served_YYYY-MM-DD.parquet               serving receipts, one row per served
                                          item (rec_served x rec_served_items)
  impressions_labeled_YYYY-MM-DD.parquet  training rows: one per SEEN item
                                          (impression event) with its serve-time
                                          score_components and the actions the
                                          user then took on it

jsonb columns are exported as JSON text; action weights are NOT applied here —
they live in the gateway config and are applied at training time (spec Part 3).

Run inside the recommendation-service image (it ships pandas + pyarrow +
psycopg), with the gateway's Postgres URL:

  docker compose run --rm -e SUPABASE_DB_URL \
    -v /opt/backend_sloco/exports:/exports \
    recommendation-service python scripts/export_event_log.py --out /exports

Without --date the previous UTC day is exported. The script is idempotent: it
overwrites the day's files.

With --retention-days N the script also DELETES exported rows older than N days
after a successful export (Postgres is the buffer, parquet is the archive —
TASKS_52). Deletion is per UTC day and only for days whose three files exist in
--out; a day without files is kept and reported. identity_links is never touched.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg

EVENTS_SQL = """
select
  id, event_id::text, event_type, known_type, user_id, anon_id, session_id,
  surface, request_id::text, position, place_id, client_ts, server_ts, seq,
  context::text, payload::text, device::text
from public.events_raw
where server_ts >= %(day_start)s and server_ts < %(day_end)s
order by id
"""

SERVED_SQL = """
select
  s.request_id::text, s.user_id, s.server_ts, s.surface, s.city,
  s.algorithm_version, s.weights_preset, s.value_weights_version,
  s.config_overrides::text, s.profiles_count, s.fallback_used, s.latency_ms,
  i.position, i.place_id, i.profile_id, i.score, i.score_components::text
from public.rec_served s
join public.rec_served_items i using (request_id)
where s.server_ts >= %(day_start)s and s.server_ts < %(day_end)s
order by s.server_ts, s.request_id, i.position
"""

# One row per SEEN (impression) serving item; actions on the same
# (request_id, place_id) are aggregated next to it. Actions may trail the
# impression by minutes, so they are read WITHOUT the day cut — the impression's
# server_ts anchors the row to the day.
IMPRESSIONS_LABELED_SQL = """
with day_impressions as (
  select distinct on (request_id, place_id)
    request_id, place_id, user_id, anon_id, session_id, surface, server_ts
  from public.events_raw
  where event_type = 'impression'
    and request_id is not null
    and place_id is not null
    and server_ts >= %(day_start)s and server_ts < %(day_end)s
  order by request_id, place_id, server_ts
),
actions as (
  select
    request_id,
    place_id,
    array_agg(distinct event_type order by event_type) as action_types,
    max((payload->>'dwell_ms')::bigint)
      filter (where event_type = 'card_dwell') as dwell_ms
  from public.events_raw
  where event_type <> 'impression'
    and request_id is not null
    and place_id is not null
  group by request_id, place_id
)
select
  imp.request_id::text, imp.place_id,
  coalesce(imp.user_id, s.user_id) as user_id,
  imp.anon_id, imp.session_id, imp.surface, imp.server_ts as impression_ts,
  i.position, i.profile_id, i.score, i.score_components::text,
  s.algorithm_version, s.weights_preset, s.value_weights_version,
  s.fallback_used,
  a.action_types, a.dwell_ms
from day_impressions imp
left join public.rec_served s on s.request_id = imp.request_id
left join public.rec_served_items i
  on i.request_id = imp.request_id and i.place_id = imp.place_id
left join actions a
  on a.request_id = imp.request_id and a.place_id = imp.place_id
order by imp.server_ts
"""


# Days (UTC) older than the retention cutoff that still hold rows, per table.
CLEANUP_DAYS_SQL = """
select day from (
  select distinct (server_ts at time zone 'UTC')::date as day from public.events_raw
  union
  select distinct (server_ts at time zone 'UTC')::date as day from public.rec_served
) as days
where day < %(cutoff_day)s
order by day
"""

# rec_served_items has no timestamp of its own: delete through its serving header.
CLEANUP_ITEMS_SQL = """
delete from public.rec_served_items as i
using public.rec_served as s
where i.request_id = s.request_id
  and s.server_ts >= %(day_start)s and s.server_ts < %(day_end)s
"""

CLEANUP_SERVED_SQL = """
delete from public.rec_served
where server_ts >= %(day_start)s and server_ts < %(day_end)s
"""

CLEANUP_EVENTS_SQL = """
delete from public.events_raw
where server_ts >= %(day_start)s and server_ts < %(day_end)s
"""


def day_window(day: date) -> dict[str, datetime]:
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return {"day_start": day_start, "day_end": day_start + timedelta(days=1)}


def day_files(day: date) -> list[str]:
    return [
        f"events_{day.isoformat()}.parquet",
        f"served_{day.isoformat()}.parquet",
        f"impressions_labeled_{day.isoformat()}.parquet",
    ]


def export_day(
    connection: psycopg.Connection, day: date, out_dir: Path
) -> None:
    params = day_window(day)
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = dict(
        zip(
            day_files(day),
            [EVENTS_SQL, SERVED_SQL, IMPRESSIONS_LABELED_SQL],
            strict=True,
        )
    )

    for file_name, sql in outputs.items():
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [column.name for column in cursor.description or []]
            frame = pd.DataFrame(cursor.fetchall(), columns=columns)
        # array_agg comes back as a python list; parquet stores it natively.
        target = out_dir / file_name
        frame.to_parquet(target, index=False)
        print(f"{target}: {len(frame)} rows")


def cleanup_exported_days(
    connection: psycopg.Connection,
    out_dir: Path,
    retention_days: int,
    today: date,
) -> None:
    """Delete exported rows older than the retention window (TASKS_52).

    Postgres is a buffer here, not the archive: the parquet files are the
    durable copy. Deletion is per UTC day, inside one transaction per day, and
    ONLY for days whose three parquet files are present in ``out_dir`` — a day
    whose export is missing (failed cron, moved files) is kept and reported, so
    the failure mode is "data stays", never "data lost". ``identity_links`` is
    not exported and is never touched.
    """
    cutoff_day = today - timedelta(days=retention_days)
    with connection.cursor() as cursor:
        cursor.execute(CLEANUP_DAYS_SQL, {"cutoff_day": cutoff_day})
        days = [row[0] for row in cursor.fetchall()]

    if not days:
        print(f"cleanup: nothing older than {cutoff_day.isoformat()}")
        return

    for day in days:
        missing = [name for name in day_files(day) if not (out_dir / name).exists()]
        if missing:
            print(
                f"cleanup: KEEPING {day.isoformat()} — export files missing "
                f"({', '.join(missing)}); run --date {day.isoformat()} first"
            )
            continue

        params = day_window(day)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(CLEANUP_ITEMS_SQL, params)
                items = cursor.rowcount
                cursor.execute(CLEANUP_SERVED_SQL, params)
                served = cursor.rowcount
                cursor.execute(CLEANUP_EVENTS_SQL, params)
                events = cursor.rowcount
        print(
            f"cleanup: {day.isoformat()} deleted — events {events}, "
            f"servings {served}, items {items}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="UTC day to export (YYYY-MM-DD); default: yesterday",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/exports"),
        help="output directory (default: /exports)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL"),
        help="Postgres URL; default: $SUPABASE_DB_URL or $DATABASE_URL",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help=(
            "after a successful export, DELETE exported rows older than N days "
            "(per-day, only days whose parquet files exist in --out); "
            "default: no cleanup"
        ),
    )
    args = parser.parse_args()

    if not args.database_url:
        print(
            "No database URL: pass --database-url or set SUPABASE_DB_URL",
            file=sys.stderr,
        )
        return 2
    if args.retention_days is not None and args.retention_days < 2:
        # Late events from offline devices reference day-old servings; a window
        # under 2 days would delete the servings their labels need.
        print("--retention-days must be at least 2", file=sys.stderr)
        return 2

    today = datetime.now(UTC).date()
    day = args.date or (today - timedelta(days=1))
    with psycopg.connect(args.database_url) as connection:
        export_day(connection, day, args.out)
        # Cleanup runs ONLY after the export above succeeded.
        if args.retention_days is not None:
            cleanup_exported_days(connection, args.out, args.retention_days, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
