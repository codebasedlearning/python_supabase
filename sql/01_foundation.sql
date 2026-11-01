-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 01_foundation.sql — the data schema, the API role, extensions, helpers
--
--   mobappdata      : ALL tables + internal functions. Not exposed to the API,
--                     no privileges for anon / authenticated.
--   public          : the API — views to read, RPC functions to act (07, 08).
--   mobappdata_api  : the role that OWNS the API (views + RPC functions).
--                     Not the table owner, no BYPASSRLS, no login → the API
--                     runs with exactly the rights granted in 09, and RLS
--                     applies to it.
-- =============================================================================

-- Roles are cluster-wide and survive DROP SCHEMA → create only if missing.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'mobappdata_api') then
    create role mobappdata_api nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
  end if;
end $$;
comment on role mobappdata_api is 'Owner of the API (public views + RPC functions) of this project.';
grant mobappdata_api to postgres;   -- lets the setup scripts hand over ownership (09)

create schema mobappdata authorization postgres;
comment on schema mobappdata is 'All app data. Reachable only through the views and RPC functions in public.';

revoke all on schema mobappdata from public, anon, authenticated;

-- Supabase keeps extensions in their own schema.
create schema if not exists extensions;
create extension if not exists pg_trgm with schema extensions;

-- Which numbered scripts were applied, and when (written by `sbcli db ...`).
create table mobappdata.schema_scripts (
  file_name   text primary key,
  checksum    text not null,
  applied_at  timestamptz not null default now()
);
comment on table mobappdata.schema_scripts is 'Log of applied sql/NN_*.sql scripts (maintained by sbcli).';

-- Generic "touch updated_at" trigger function.
create or replace function mobappdata.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- The caller's user id from the request JWT — same logic as Supabase's
-- auth.uid(), but in our own schema: the API role needs no rights in `auth`.
create or replace function mobappdata.current_user_id()
returns uuid
language sql
stable
set search_path = ''
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

-- Guard used by every RPC function: "you must be signed in".
create or replace function mobappdata.require_user()
returns uuid
language plpgsql
stable
set search_path = ''
as $$
declare
  v_uid uuid := mobappdata.current_user_id();
begin
  if v_uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  return v_uid;
end;
$$;
