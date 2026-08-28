-- Undo for migration 023 (TASKS_54). Restores the pre-023 shape: one default
-- collection (Favorites), no system slugs.
--
-- ⚠️ WARNING: THIS SCRIPT DELETES the two system collections it created (Saved and
-- Been there) TOGETHER WITH THEIR MEMBERSHIP ROWS (saved_collection_places cascades
-- on collection delete). Places stay in public.saved_places; only the list
-- membership is lost. Run it only to undo 023.

update public.saved_collections set is_default = false where slug = 'saved';
update public.saved_collections set is_default = true  where slug = 'favorites';

delete from public.saved_collections where slug in ('saved', 'been');

update public.saved_collections set slug = null where slug = 'favorites';

drop index if exists public.saved_collections_user_slug_idx;

alter table public.saved_collections drop column if exists slug;
