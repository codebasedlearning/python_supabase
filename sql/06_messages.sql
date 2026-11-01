-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 06_messages.sql — one simple message board for all signed-in users
--   read all          : view public.messages
--   post              : RPC post_message
--   edit / delete own : RPC edit_message / delete_message  (rule lives there, see 08)
--   live notification : Realtime → 09_security.sql + 10_realtime.sql
-- =============================================================================

create table mobappdata.messages (
  id          bigint generated always as identity primary key,
  author_id   uuid references mobappdata.profiles (id) on delete set null,
  body        text not null check (char_length(btrim(body, E' \t\r\n')) between 1 and 2000),
  created_at  timestamptz not null default now(),
  edited_at   timestamptz                -- null = never edited
);
create index messages_created_idx on mobappdata.messages (created_at desc);
comment on table mobappdata.messages is 'Messages. Author is deleted → message stays as "(deleted user)".';
