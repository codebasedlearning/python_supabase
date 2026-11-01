-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 02_profiles.sql — profiles + audit log, fed by triggers on auth.users
--
--   auth.users INSERT  -> profiles row created,   audit 'created'
--   auth.users UPDATE  -> email / sign-in synced, audit 'email_changed' | 'signed_in'
--   auth.users DELETE  -> profiles row cascades,  audit 'deleted'
-- =============================================================================

create table mobappdata.profiles (
  id               uuid primary key references auth.users (id) on delete cascade,
  email            text,
  display_name     text not null,
  bio              text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  last_sign_in_at  timestamptz
);
comment on table mobappdata.profiles is 'One row per auth user. Maintained by triggers on auth.users.';

create trigger profiles_updated_at
  before update on mobappdata.profiles
  for each row execute function mobappdata.set_updated_at();

create table mobappdata.user_audit (
  id       bigint generated always as identity primary key,
  user_id  uuid not null,            -- no FK on purpose: survives the user's deletion
  email    text,
  action   text not null check (action in ('created', 'email_changed', 'signed_in', 'deleted')),
  details  jsonb not null default '{}'::jsonb,
  at       timestamptz not null default now()
);
create index user_audit_user_at_idx on mobappdata.user_audit (user_id, at desc);
comment on table mobappdata.user_audit is 'Append-only log of lifecycle events on auth.users.';

-- ----------------------------------------------------------------------------
-- Trigger functions. SECURITY DEFINER because the auth service (role
-- supabase_auth_admin) fires them and must be able to write into mobappdata.
-- ----------------------------------------------------------------------------
create or replace function mobappdata.handle_auth_user_created()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into mobappdata.profiles (id, email, display_name, created_at, last_sign_in_at)
  values (
    new.id,
    new.email,
    coalesce(nullif(btrim(new.raw_user_meta_data ->> 'display_name'), ''),
             split_part(coalesce(new.email, 'user'), '@', 1)),
    coalesce(new.created_at, now()),
    new.last_sign_in_at
  )
  on conflict (id) do nothing;

  insert into mobappdata.user_audit (user_id, email, action, details)
  values (new.id, new.email, 'created',
          jsonb_build_object('provider', new.raw_app_meta_data ->> 'provider'));
  return new;
end;
$$;

create or replace function mobappdata.handle_auth_user_updated()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.email is distinct from old.email then
    update mobappdata.profiles set email = new.email where id = new.id;
    insert into mobappdata.user_audit (user_id, email, action, details)
    values (new.id, new.email, 'email_changed', jsonb_build_object('old_email', old.email));
  end if;

  if new.last_sign_in_at is distinct from old.last_sign_in_at and new.last_sign_in_at is not null then
    update mobappdata.profiles set last_sign_in_at = new.last_sign_in_at where id = new.id;
    insert into mobappdata.user_audit (user_id, email, action)
    values (new.id, new.email, 'signed_in');
  end if;

  if (new.raw_user_meta_data ->> 'display_name') is distinct from (old.raw_user_meta_data ->> 'display_name')
     and nullif(btrim(new.raw_user_meta_data ->> 'display_name'), '') is not null then
    update mobappdata.profiles set display_name = btrim(new.raw_user_meta_data ->> 'display_name') where id = new.id;
  end if;
  return new;
end;
$$;

create or replace function mobappdata.handle_auth_user_deleted()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into mobappdata.user_audit (user_id, email, action)
  values (old.id, old.email, 'deleted');
  return old;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function mobappdata.handle_auth_user_created();

create trigger on_auth_user_updated
  after update on auth.users
  for each row execute function mobappdata.handle_auth_user_updated();

create trigger on_auth_user_deleted
  after delete on auth.users
  for each row execute function mobappdata.handle_auth_user_deleted();

-- Back-fill: users that survived a reset get their profile back.
insert into mobappdata.profiles (id, email, display_name, created_at, last_sign_in_at)
select u.id,
       u.email,
       coalesce(nullif(btrim(u.raw_user_meta_data ->> 'display_name'), ''),
                split_part(coalesce(u.email, 'user'), '@', 1)),
       coalesce(u.created_at, now()),
       u.last_sign_in_at
from auth.users u
on conflict (id) do nothing;
