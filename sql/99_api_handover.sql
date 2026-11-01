-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 99_api_handover.sql — ALWAYS the last script (sbcli re-runs it after every
-- `db apply` / `db reset`, so scripts you add later — 11_…, 12_… — are covered).
--
-- 1. every view and function in public is handed over to mobappdata_api
--    (SECURITY DEFINER = "run as mobappdata_api", see 09)
-- 2. the API is for signed-in users only: SELECT on views, EXECUTE on functions
-- Idempotent.
-- =============================================================================

-- =============================================================================
-- 1. hand the API over to mobappdata_api
-- =============================================================================
-- (the new owner needs CREATE on the schema for ALTER … OWNER; taken back below)
grant create on schema public to mobappdata_api;

do $$
declare
  r record;
begin
  for r in select c.oid::regclass::text as obj
           from pg_class c
           where c.relnamespace = 'public'::regnamespace and c.relkind = 'v' loop
    execute format('alter view %s owner to mobappdata_api', r.obj);
  end loop;

  for r in select p.oid::regprocedure::text as obj
           from pg_proc p
           where p.pronamespace = 'public'::regnamespace loop
    execute format('alter function %s owner to mobappdata_api', r.obj);
  end loop;
end $$;

revoke create on schema public from mobappdata_api;

-- =============================================================================
-- 2. who may use the API
-- =============================================================================
-- the API: views readable, functions callable — signed in only
revoke all    on all tables    in schema public from public, anon;
grant  select on all tables    in schema public to authenticated, service_role;

revoke all     on all functions in schema public from public, anon;
grant  execute on all functions in schema public to authenticated, service_role;
