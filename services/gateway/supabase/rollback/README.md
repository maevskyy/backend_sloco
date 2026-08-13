# Rollback scripts

Undo scripts for migrations whose *contract* can break the running app — in
practice: dropped/replaced RPC signatures. They are **not** part of the
migration sequence and must never be run as one.

Add a script here only when a migration replaces a function the gateway calls.
Additive migrations (a new column, a new index) need nothing: nothing to undo.

Naming:

```text
YYYY-MM-DD_<migrations>_rollback.sql
```

Rules:

- A rollback restores the **previous function bodies verbatim** (copy them from
  the migration that introduced them, and say which one in a comment).
- A rollback must be **idempotent** — safe to run twice.
- A rollback must **not drop columns or data.** Columns added by the forward
  migration stay; once the old trigger/function is back they are simply inert.
  Removing them is a separate, reviewed change.
- After running one, redeploy the matching gateway version — the code and the
  RPC signatures are one contract.

## Current

- `2026-08-11_018_019_rollback.sql` — undoes `018` (search category/radius +
  stored `*_norm` columns) and `019` (feed fallback category). Restores
  `search_places` from migration `011`, `feed_fallback_places` from `016`, the
  pre-018 keyword trigger and the `places_name_trgm` index.
- `2026-08-13_021_feed_city_cut_rollback.sql` — undoes `021` (feed city hard
  cut). Restores `feed_fallback_places` from migration `020`.
