-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 09_security.sql — the border, enforced
--
-- Three layers:
--   1. ARCHITECTURE  The API (views + RPC in public) is the only way in.
--                    mobappdata is not exposed; anon / authenticated have no
--                    privileges there.
--   2. API ROLE      Views and RPC functions are owned by mobappdata_api (99), so
--                    SECURITY DEFINER means "run as mobappdata_api" — a role
--                    with exactly the grants below, no BYPASSRLS, not the
--                    table owner. A bug in a function can't drop tables, read
--                    auth.users or write the audit log.
--   3. RLS           Applies to mobappdata_api. Deliberately simple: one open
--                    policy per table, strict policies only for the one-line
--                    "it's yours" rules. Business logic spanning tables stays
--                    in the functions (08); RLS catches a forgotten WHERE.
--
-- Who may do what:
--   anon           → nothing (no view, no function, no table)
--   authenticated  → SELECT on views + EXECUTE on RPC functions in public.
--                    In mobappdata only: SELECT on messages (Realtime delivers
--                    a row only to subscribers that may SELECT the table).
--   service_role   → same as authenticated in public (admin work goes through
--                    the Auth admin API anyway)
--   postgres       → owns the tables; the auth.users triggers and the two
--                    narrow helpers storage_object_exists() / name_similarity()
--                    run as postgres.
-- =============================================================================

-- =============================================================================
-- privileges of the API role (exactly what the views + functions need)
-- =============================================================================
grant usage on schema mobappdata to mobappdata_api;
grant usage on schema public     to mobappdata_api;   -- functions read the API views (search_products)

grant select, insert, update, delete on mobappdata.categories, mobappdata.products,
                                        mobappdata.pictures,   mobappdata.messages
  to mobappdata_api;
grant select, update on mobappdata.profiles   to mobappdata_api;   -- insert/delete: auth triggers only
grant select         on mobappdata.user_audit to mobappdata_api;   -- written by auth triggers only
-- (no access to mobappdata.schema_scripts — that's bookkeeping for sbcli)

grant usage, select, update on all sequences in schema mobappdata to mobappdata_api;  -- identity + setval

-- internal helpers the API calls (they run as the caller = mobappdata_api)
grant execute on function mobappdata.current_user_id(),
                          mobappdata.require_user(),
                          mobappdata.category_id(text),
                          mobappdata.upsert_category(text, integer),
                          mobappdata.upsert_product(integer, text, text, text, numeric, numeric),
                          mobappdata.sync_id_sequences(),
                          mobappdata.storage_object_exists(text),
                          mobappdata.name_similarity(text, text)
  to mobappdata_api;

-- =============================================================================
-- 3. RLS — on every table; policies for the API role
-- =============================================================================
alter table mobappdata.schema_scripts enable row level security;   -- no policies = closed
alter table mobappdata.profiles       enable row level security;
alter table mobappdata.user_audit     enable row level security;
alter table mobappdata.categories     enable row level security;
alter table mobappdata.products       enable row level security;
alter table mobappdata.pictures       enable row level security;
alter table mobappdata.messages       enable row level security;

-- open: the functions decide (validation, cross-table rules)
create policy api_all on mobappdata.categories for all to mobappdata_api using (true) with check (true);
create policy api_all on mobappdata.products   for all to mobappdata_api using (true) with check (true);
create policy api_all on mobappdata.pictures   for all to mobappdata_api using (true) with check (true);

-- read-only
create policy api_read on mobappdata.user_audit for select to mobappdata_api using (true);

-- "it's yours": the database enforces it, even if a function forgets the WHERE
create policy api_read       on mobappdata.profiles for select to mobappdata_api using (true);
create policy api_update_own on mobappdata.profiles for update to mobappdata_api
  using (id = mobappdata.current_user_id()) with check (id = mobappdata.current_user_id());

create policy api_read       on mobappdata.messages for select to mobappdata_api using (true);
create policy api_insert_own on mobappdata.messages for insert to mobappdata_api
  with check (author_id = mobappdata.current_user_id());
create policy api_update_own on mobappdata.messages for update to mobappdata_api
  using (author_id = mobappdata.current_user_id()) with check (author_id = mobappdata.current_user_id());
create policy api_delete_own on mobappdata.messages for delete to mobappdata_api
  using (author_id = mobappdata.current_user_id());

-- =============================================================================
-- 1. the outer wall: API users
-- =============================================================================
revoke all on all tables    in schema mobappdata from public, anon, authenticated, service_role;
revoke all on all sequences in schema mobappdata from public, anon, authenticated, service_role;
revoke all on all functions in schema mobappdata from public, anon, authenticated, service_role;

-- the one exception: Realtime on messages (read-only, same data as public.messages)
grant usage  on schema mobappdata          to authenticated;
grant select on table  mobappdata.messages to authenticated;
create policy messages_realtime_read on mobappdata.messages
  for select to authenticated using (true);

-- Postgres checks functions called INSIDE a view against the caller, not the
-- view owner → API users may call current_user_id() (it only returns their
-- own id; used by view public.messages for `mine`).
grant usage   on schema mobappdata to service_role;
grant execute on function mobappdata.current_user_id() to authenticated, service_role;

-- (ownership of the API + grants on public: 99_api_handover.sql, always the last script)
