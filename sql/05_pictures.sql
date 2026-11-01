-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 05_pictures.sql — storage bucket "pictures" (S3-backed) + managing table
--
-- The binary lives in Supabase Storage (bucket `pictures`), the metadata and
-- the link to a product in mobappdata.pictures. The CLI keeps both in sync.
-- Storage is the one place a user acts directly (the Storage API runs as the
-- user), hence the policies on storage.objects.
-- =============================================================================

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('pictures', 'pictures', false, 10 * 1024 * 1024,
        array['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml', 'image/avif'])
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

create policy "pictures: authenticated read"   on storage.objects
  for select to authenticated using (bucket_id = 'pictures');
create policy "pictures: authenticated insert" on storage.objects
  for insert to authenticated with check (bucket_id = 'pictures');
create policy "pictures: authenticated update" on storage.objects
  for update to authenticated using (bucket_id = 'pictures') with check (bucket_id = 'pictures');
create policy "pictures: authenticated delete" on storage.objects
  for delete to authenticated using (bucket_id = 'pictures');

create table mobappdata.pictures (
  id           bigint generated always as identity primary key,
  bucket_id    text not null default 'pictures',
  object_path  text not null,
  file_name    text not null,
  title        text,
  mime_type    text,
  size_bytes   bigint check (size_bytes is null or size_bytes >= 0),
  product_id   integer references mobappdata.products (id) on delete set null,
  is_primary   boolean not null default false,
  uploaded_by  uuid references mobappdata.profiles (id) on delete set null,
  created_at   timestamptz not null default now(),
  unique (bucket_id, object_path),
  constraint pictures_primary_needs_product check (not is_primary or product_id is not null)
);
create index pictures_product_idx on mobappdata.pictures (product_id);
create unique index pictures_one_primary_per_product
  on mobappdata.pictures (product_id) where is_primary;
comment on table mobappdata.pictures is 'Metadata for objects in storage bucket "pictures".';
