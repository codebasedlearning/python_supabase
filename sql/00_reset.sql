-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 00_reset.sql — the big red button.
--
-- Removes EVERYTHING this project created:
--   * schema mobappdata  (all tables, data, internal functions — and with them
--                         the triggers on auth.users, via CASCADE)
--   * schema public      (the API: views + RPC functions)
--   * storage policies + the `pictures` bucket
--   * role mobappdata_api (owner of the API, re-created by 01)
--
-- It does NOT delete auth users (see sql/extra/wipe_auth_users.sql, or use
-- `sbcli db reset --wipe-users`). Profiles are back-filled from auth.users by
-- 02_profiles.sql, so keeping users after a reset stays consistent.
--
-- Hosted Supabase blocks plain DELETEs on storage tables. `sbcli db reset`
-- therefore empties + deletes the bucket through the Storage API first; the
-- SQL below is only a best-effort fallback and never aborts the reset.
-- =============================================================================
set client_min_messages = warning;

-- --- storage: policies live on storage.objects ---------------------------------
drop policy if exists "pictures: authenticated read"   on storage.objects;
drop policy if exists "pictures: authenticated insert" on storage.objects;
drop policy if exists "pictures: authenticated update" on storage.objects;
drop policy if exists "pictures: authenticated delete" on storage.objects;

do $$
begin
  delete from storage.objects where bucket_id = 'pictures';
exception when others then
  raise warning 'storage.objects cleanup skipped (%): empty the bucket via Storage API', sqlerrm;
end $$;

do $$
begin
  delete from storage.buckets where id = 'pictures';
exception when others then
  raise warning 'bucket "pictures" not deleted (%): it will simply be reused', sqlerrm;
end $$;

-- --- data schema ---------------------------------------------------------------
drop schema if exists mobappdata cascade;

-- --- API schema: nuke & rebuild ------------------------------------------------
drop schema if exists public cascade;
create schema public authorization postgres;
comment on schema public is 'API surface: read views + RPC actions. No tables here — data lives in mobappdata.';

grant usage on schema public to postgres, anon, authenticated, service_role;

-- Whatever gets created here later is for signed-in users (+ service role) only.
-- (No defaults for anon — the anonymous key can't read or call anything.)
alter default privileges in schema public grant select  on tables    to authenticated, service_role;
alter default privileges in schema public grant execute on functions to authenticated, service_role;

-- --- API owner role (cluster-wide, so it is removed explicitly) -----------------
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'mobappdata_api') then
    execute 'drop owned by mobappdata_api';
    execute 'drop role mobappdata_api';
  end if;
exception when others then
  raise warning 'role mobappdata_api not dropped (%): it will simply be reused', sqlerrm;
end $$;
