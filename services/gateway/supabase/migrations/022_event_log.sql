-- TASKS_51: event log MVP (x-algorithm handoff spec sloco_event_log_backend_spec.md).
--
-- Non-destructive: CREATE TABLE / CREATE INDEX only. No existing object is
-- touched. Four new tables:
--
--   events_raw       append-only telemetry facts from the app (POST /v1/events).
--                    Rows are NEVER updated or deleted (spec rule). event_type is
--                    text on purpose: new client event types must not need a
--                    migration; unknown ones land with known_type = false.
--   identity_links   anon device id -> account id, written when an authenticated
--                    batch carries anon_id. Old events keep their anon_id; readers
--                    join through this table (spec: no backfilling updates).
--   rec_served       one row per recommendation serving ("receipt"), written by
--                    the gateway AFTER the response is sent (spec 2.0).
--   rec_served_items one row per served place: final position (0-based) and the
--                    full score_components captured at serve time.
--
-- request_id has NO foreign key by design: an event can arrive before the
-- serving row is written (async insert), and must never be rejected for it.
--
-- Deviations from the spec SQL, both additive:
--   * events_raw.context jsonb — the frontend envelope's context object carries
--     mode/city/profile_id beyond the extracted request_id/position columns;
--     dropping them would lose facts.
--   * id bigserial is a PRIMARY KEY (spec listed it as a bare column).
--
-- RLS is enabled with no policies on all four tables: PostgREST (anon key) can
-- read nothing; the gateway writes through its direct pg pool, which bypasses
-- RLS. Same pattern as place_reactions (migration 015).

create table if not exists public.events_raw (
  id            bigserial primary key,
  event_id      uuid unique not null,   -- dedupe guard: retried batches reuse ids
  event_type    text not null,          -- not an enum: new types arrive without migrations
  known_type    boolean not null,
  user_id       text,                   -- from the auth token only; null before login
  anon_id       text,
  session_id    text,
  surface       text,
  request_id    uuid,                   -- serving reference; deliberately no FK
  position      int,
  place_id      text,
  client_ts     timestamptz,
  server_ts     timestamptz not null,
  seq           bigint,
  context       jsonb,                  -- envelope context beyond the extracted columns
  payload       jsonb,                  -- type-specific extras, stored as sent
  device        jsonb                   -- from the batch envelope, copied per event
);

create index if not exists events_raw_user_server_ts_idx
  on public.events_raw (user_id, server_ts);

create index if not exists events_raw_place_event_type_idx
  on public.events_raw (place_id, event_type);

create index if not exists events_raw_request_id_idx
  on public.events_raw (request_id);

-- Daily export job scans by server_ts range.
create index if not exists events_raw_server_ts_idx
  on public.events_raw (server_ts);

create table if not exists public.identity_links (
  anon_id    text not null,
  user_id    text not null,
  linked_at  timestamptz not null default now(),

  primary key (anon_id, user_id)
);

create table if not exists public.rec_served (
  request_id            uuid primary key,
  user_id               text,
  server_ts             timestamptz not null,
  surface               text,          -- feed / similar / onboarding
  city                  text,
  algorithm_version     text,
  weights_preset        text,          -- blend-weights preset active for this serving
  value_weights_version text,          -- action "price list" config version (gateway)
  config_overrides      jsonb,         -- non-default knobs for this request ({} = defaults)
  profiles_count        int,
  fallback_used         boolean,
  latency_ms            int
);

create index if not exists rec_served_user_server_ts_idx
  on public.rec_served (user_id, server_ts);

-- Daily export job scans by server_ts range.
create index if not exists rec_served_server_ts_idx
  on public.rec_served (server_ts);

create table if not exists public.rec_served_items (
  request_id        uuid not null,
  position          int not null,       -- 0-based final position in the serving
  place_id          text not null,
  profile_id        int,
  score             double precision,
  score_components  jsonb,              -- FULL breakdown captured at serve time

  primary key (request_id, position)
);

alter table public.events_raw enable row level security;
alter table public.identity_links enable row level security;
alter table public.rec_served enable row level security;
alter table public.rec_served_items enable row level security;
