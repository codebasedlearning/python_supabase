-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- extra/wipe_auth_users.sql — deletes ALL auth users (identities, sessions,
-- refresh tokens cascade inside the auth schema).
-- Run by `sbcli db reset --wipe-users`, never automatically. Not numbered
-- on purpose: it is not part of the regular setup chain.
-- =============================================================================
delete from auth.users;
