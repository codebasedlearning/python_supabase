-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 10_realtime.sql — push new messages over Supabase Realtime
-- (`sbcli chat listen` subscribes to INSERT / UPDATE / DELETE on mobappdata.messages;
--  delivery is checked per subscriber → signed-in users only, see 09.
--  DELETE events carry only the id — enough for "message #12 was deleted".)
-- =============================================================================
do $$
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    create publication supabase_realtime;
  end if;

  if not exists (select 1 from pg_publication_tables
                 where pubname = 'supabase_realtime' and schemaname = 'mobappdata' and tablename = 'messages') then
    alter publication supabase_realtime add table mobappdata.messages;
  end if;
end;
$$;
