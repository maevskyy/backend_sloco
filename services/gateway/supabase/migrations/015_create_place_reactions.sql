create table if not exists public.place_reactions (
  user_id uuid not null references auth.users(id) on delete cascade,
  source_id text not null,
  reaction text not null check (reaction in ('favorite', 'dislike', 'hide')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  primary key (user_id, source_id)
);

create index if not exists place_reactions_user_reaction_idx
  on public.place_reactions (user_id, reaction);

alter table public.place_reactions enable row level security;
