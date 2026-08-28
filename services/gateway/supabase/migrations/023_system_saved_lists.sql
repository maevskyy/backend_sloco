-- TASKS_54: three SYSTEM saved lists — Saved, Favorites, Been there.
--
-- ⚠️ WARNING: THIS MIGRATION WRITES TO EXISTING ROWS of public.saved_collections
-- (it fills the new `slug` column, moves the `is_default` flag, and inserts two
-- new rows per user). It does NOT drop or delete anything, and every statement is
-- guarded + idempotent, so a re-run is a no-op. Undo:
-- supabase/rollback/2026-08-28_023_system_lists_rollback.sql.
--
-- Why: saving a place used to file it into the ONE default collection, so the app
-- could not ask "which list?". The user now picks lists explicitly, and three of
-- them are system lists that exist for everybody, cannot be deleted, and are
-- hidden from "My lists": Saved (the quick-save bucket), Favorites, Been there.
--
-- `slug` is the stable identity behind those three (the NAME is display text and
-- may be localized later). It stays NULL for user-created lists, so the partial
-- unique index below only constrains system rows.
--
-- `is_default` keeps its meaning — "where a save with no explicit list goes" — and
-- moves from the old default to `saved`. Recommender consequence, handled in the
-- same change (services/gateway/src/modules/feed/stores/feed.store.ts): the
-- default list stays the weak `want_to_go` signal, so quick saves keep today's
-- weight; Favorites becomes the strong `favourites` signal; Been there is
-- excluded from taste signals entirely (a visit is not a preference).

alter table public.saved_collections
  add column if not exists slug text;

create unique index if not exists saved_collections_user_slug_idx
  on public.saved_collections (user_id, slug)
  where slug is not null;

-- 1. The collection that is default TODAY is the user's Favorites (migration
--    TASKS_53 already renamed it); label it so.
update public.saved_collections
   set slug = 'favorites'
 where is_default = true
   and slug is null;

-- 2a. Adopt a list the user already created under a system name, instead of
--     colliding with `unique (user_id, name)`. Without this the user would end up
--     with NO default list at all: step 3 moves `is_default` onto slug='saved',
--     and that row would never have been created for them.
update public.saved_collections as target
   set slug = 'saved'
 where target.slug is null
   and target.name = 'Saved'
   and not exists (
         select 1 from public.saved_collections other
          where other.user_id = target.user_id and other.slug = 'saved'
       );

update public.saved_collections as target
   set slug = 'been'
 where target.slug is null
   and target.name = 'Been there'
   and not exists (
         select 1 from public.saved_collections other
          where other.user_id = target.user_id and other.slug = 'been'
       );

-- 2b. Give every remaining user the two missing system lists.
insert into public.saved_collections (user_id, name, slug, is_default, sort_order)
select c.user_id, 'Saved', 'saved', false, 0
  from (select distinct user_id from public.saved_collections) as c
 where not exists (
         select 1 from public.saved_collections existing
          where existing.user_id = c.user_id
            and (existing.slug = 'saved' or existing.name = 'Saved')
       );

insert into public.saved_collections (user_id, name, slug, is_default, sort_order)
select c.user_id, 'Been there', 'been', false, 2
  from (select distinct user_id from public.saved_collections) as c
 where not exists (
         select 1 from public.saved_collections existing
          where existing.user_id = c.user_id
            and (existing.slug = 'been' or existing.name = 'Been there')
       );

-- 3. Move the default flag Favorites -> Saved. Order matters: the partial unique
--    index allows only one default per user, so clear before setting.
update public.saved_collections set is_default = false where slug = 'favorites';
update public.saved_collections set is_default = true  where slug = 'saved';

-- 4. Keep the system lists at the top of any sort_order listing.
update public.saved_collections set sort_order = 0 where slug = 'saved';
update public.saved_collections set sort_order = 1 where slug = 'favorites';
update public.saved_collections set sort_order = 2 where slug = 'been';
