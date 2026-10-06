-- SLO-69: user reviews of places — one review per user per place.
--
-- ADDITIVE ONLY: creates one new table and one index. No existing table, row,
-- function or index is touched; `if not exists` makes a re-run a no-op.
-- Undo: drop table public.place_reviews;
--
-- Why: the iOS redesign (Profile → My ratings & reviews, the place sheet) keeps
-- reviews in memory today, so they vanish when the app quits.
--
-- Keyed by (place_source, place_source_id) — the catalog's stable identity —
-- not by places.id, so a review survives a catalog reimport (same reasoning as
-- place_reactions, docs/DECISIONS.md). The API still speaks places.id.
--
-- Only `rating` is checked here. The tag vocabulary and the 2000-character text
-- limit are validated in the gateway (Zod), so adding a tag is a code change,
-- not a migration. `helpful_count` stays 0 until there is a "mark helpful"
-- action. Photos are a later stage (SLO-70) with their own table.

create table if not exists public.place_reviews (
  user_id uuid not null references auth.users(id) on delete cascade,
  place_source text not null,
  place_source_id text not null,
  rating smallint not null check (rating between 1 and 5),
  body text not null default '',
  tags text[] not null default '{}',
  helpful_count integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  primary key (user_id, place_source, place_source_id)
);

-- GET /v1/me/reviews: the user's reviews, newest first.
create index if not exists place_reviews_user_created_idx
  on public.place_reviews (user_id, created_at desc);

alter table public.place_reviews enable row level security;
