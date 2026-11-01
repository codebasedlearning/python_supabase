-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 08_api_functions.sql — the ACTION side of the API (RPC) + internal helpers
--
--   mobappdata.*  internal helpers (not callable through the API)
--   public.*      RPC actions: client.rpc('add_product', {'p_name': …, …})
--
-- RPC functions are SECURITY DEFINER: they run as their OWNER, role
-- mobappdata_api (handed over in 09) — with exactly the privileges granted
-- there, and subject to RLS. An empty search_path means everything is
-- schema-qualified, nothing can be smuggled in. Each one starts with
-- mobappdata.require_user(): signed in, or nothing happens.
-- Parameter names are prefixed p_ (= the keys of the RPC payload).
-- =============================================================================

-- =============================================================================
-- internal helpers
-- =============================================================================

-- Two NARROW privilege escalations. They stay owned by postgres (SECURITY
-- DEFINER) and only mobappdata_api may call them — instead of granting the
-- API role access to schemas that belong to Supabase services.

-- 1. "does this object exist in the pictures bucket?" (storage.objects belongs
--    to the Storage service) — read-only yes/no.
create or replace function mobappdata.storage_object_exists(p_object_path text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (select 1 from storage.objects o
                 where o.bucket_id = 'pictures' and o.name = p_object_path)
$$;

-- 2. fuzzy name match via pg_trgm (lives in schema `extensions`, owned by a
--    Supabase admin role) — a pure computation.
create or replace function mobappdata.name_similarity(p_query text, p_name text)
returns real
language sql
immutable
security definer
set search_path = ''
as $$
  select extensions.word_similarity(p_query, p_name)
$$;

-- A category given as id ("4") or as name ("milk products", case-insensitive).
create or replace function mobappdata.category_id(p_category text)
returns integer
language plpgsql
stable
set search_path = ''
as $$
declare
  v_key text := btrim(p_category);
  v_id  integer;
begin
  if v_key ~ '^\d+$' then
    select c.id into v_id from mobappdata.categories c where c.id = v_key::integer;
  else
    select c.id into v_id from mobappdata.categories c where lower(c.name) = lower(v_key);
  end if;
  if v_id is null then
    raise exception 'unknown category "%"', p_category using errcode = 'P0002';
  end if;
  return v_id;
end;
$$;

-- Create (without id: returns the existing id if the name exists) or rename.
create or replace function mobappdata.upsert_category(p_name text, p_id integer default null)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  v_name  text := nullif(btrim(p_name), '');
  v_id    integer;
  v_other integer;
begin
  if v_name is null then
    raise exception 'category name must not be empty' using errcode = '23502';
  end if;
  if char_length(v_name) > 100 then
    raise exception 'category name longer than 100 characters' using errcode = '22001';
  end if;

  select c.id into v_other from mobappdata.categories c
  where lower(c.name) = lower(v_name) and c.id is distinct from p_id;

  if p_id is null then
    if v_other is not null then
      return v_other;                                          -- idempotent by name
    end if;
    insert into mobappdata.categories (name) values (v_name) returning id into v_id;
  else
    if v_other is not null then
      raise exception 'another category (id %) is already called "%"', v_other, v_name using errcode = '23505';
    end if;
    insert into mobappdata.categories (id, name) values (p_id, v_name)
    on conflict (id) do update set name = excluded.name
    returning id into v_id;
    perform mobappdata.sync_id_sequences();
  end if;
  return v_id;
end;
$$;

-- Create or update a product. NULL = keep; p_category 'none' clears it.
-- Friendly validation here; the CHECK constraints in 03 are the backstop.
create or replace function mobappdata.upsert_product(
  p_id       integer default null,
  p_name     text    default null,
  p_category text    default null,
  p_unit     text    default null,
  p_price    numeric default null,
  p_vat      numeric default null
)
returns integer
language plpgsql
set search_path = ''
as $$
declare
  v_id     integer := p_id;
  v_exists boolean := false;
  v_clear  boolean := lower(btrim(coalesce(p_category, ''))) in ('none', '-');
  v_cat    integer;
  v_name   text    := nullif(btrim(p_name), '');
  v_unit   text    := upper(nullif(btrim(p_unit), ''));
begin
  if p_price is not null and p_price < 0 then
    raise exception 'price must be >= 0, got %', p_price using errcode = '23514';
  end if;
  if p_vat is not null and (p_vat < 0 or p_vat >= 1) then
    raise exception 'vat is a fraction (0.07 = 7 %%), got %', p_vat using errcode = '23514';
  end if;
  if char_length(v_name) > 100 then
    raise exception 'product name longer than 100 characters' using errcode = '22001';
  end if;
  if char_length(v_unit) > 20 then
    raise exception 'unit longer than 20 characters' using errcode = '22001';
  end if;
  if p_category is not null and not v_clear then
    v_cat := mobappdata.category_id(p_category);
  end if;

  if v_id is not null then
    select true into v_exists from mobappdata.products p where p.id = v_id for update;
    v_exists := coalesce(v_exists, false);
  end if;

  if v_exists then
    update mobappdata.products p
       set name        = coalesce(v_name, p.name),
           unit        = coalesce(v_unit, p.unit),
           price       = coalesce(p_price, p.price),
           vat         = coalesce(p_vat, p.vat),
           category_id = case when v_clear then null
                              when p_category is null then p.category_id
                              else v_cat end
     where p.id = v_id;
    return v_id;
  end if;

  if v_name is null or v_unit is null then
    raise exception 'a new product needs a name and a unit' using errcode = '23502';
  end if;
  if v_id is null then
    insert into mobappdata.products (name, category_id, unit, price, vat)
    values (v_name, v_cat, v_unit, coalesce(p_price, 0.00), coalesce(p_vat, 0.19))
    returning id into v_id;
  else
    insert into mobappdata.products (id, name, category_id, unit, price, vat)
    values (v_id, v_name, v_cat, v_unit, coalesce(p_price, 0.00), coalesce(p_vat, 0.19));
    perform mobappdata.sync_id_sequences();
  end if;
  return v_id;
end;
$$;

-- =============================================================================
-- RPC: catalog
-- =============================================================================

-- Search: substring + typo-tolerant (per word) name match, filters.
-- p_category: id or name; 'none' = products without category.
create or replace function public.search_products(
  p_query     text    default null,
  p_category  text    default null,
  p_min_price numeric default null,
  p_max_price numeric default null,
  p_vat       numeric default null,
  p_unit      text    default null,
  p_limit     integer default 100,
  p_offset    integer default 0
)
returns setof public.product_details
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_q     text    := nullif(btrim(p_query), '');
  v_uncat boolean := lower(btrim(coalesce(p_category, ''))) in ('none', '-');
  v_cat   integer;
begin
  perform mobappdata.require_user();
  if p_category is not null and not v_uncat then
    v_cat := mobappdata.category_id(p_category);
  end if;

  return query
  select v.*
  from public.product_details v
  where (v_q is null
         or v.name ilike '%' || v_q || '%'
         or mobappdata.name_similarity(v_q, v.name) >= 0.5)
    and (p_category is null or (v_uncat and v.category_id is null) or v.category_id = v_cat)
    and (p_min_price is null or v.price >= p_min_price)
    and (p_max_price is null or v.price <= p_max_price)
    and (p_vat is null or v.vat = p_vat)
    and (p_unit is null or v.unit = upper(btrim(p_unit)))
  order by case when v_q is null then 0 else mobappdata.name_similarity(v_q, v.name) end desc,
           v.category_id nulls last, v.id
  limit greatest(1, least(coalesce(p_limit, 100), 1000))
  offset greatest(0, coalesce(p_offset, 0));
end;
$$;

create or replace function public.add_category(p_name text, p_id integer default null)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform mobappdata.require_user();
  return mobappdata.upsert_category(p_name, p_id);
end;
$$;

create or replace function public.rename_category(p_category text, p_name text)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform mobappdata.require_user();
  return mobappdata.upsert_category(p_name, mobappdata.category_id(p_category));
end;
$$;

-- Delete a category; its products move to p_move_to or become uncategorized.
-- Returns the number of moved products.
create or replace function public.delete_category(p_category text, p_move_to text default null)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id integer;
  v_to integer;
  v_n  integer;
begin
  perform mobappdata.require_user();
  v_id := mobappdata.category_id(p_category);
  if p_move_to is not null then
    v_to := mobappdata.category_id(p_move_to);
    if v_to = v_id then
      raise exception 'cannot move products into the category that is being deleted' using errcode = '22023';
    end if;
  end if;
  update mobappdata.products set category_id = v_to where category_id = v_id;
  get diagnostics v_n = row_count;
  delete from mobappdata.categories where id = v_id;
  return v_n;
end;
$$;

create or replace function public.add_product(
  p_name     text,
  p_unit     text,
  p_price    numeric,
  p_vat      numeric default 0.19,
  p_category text    default null,
  p_id       integer default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform mobappdata.require_user();
  if p_id is not null and exists (select 1 from mobappdata.products p where p.id = p_id) then
    raise exception 'product % exists already (use update_product)', p_id using errcode = '23505';
  end if;
  return mobappdata.upsert_product(p_id, p_name, p_category, p_unit, p_price, p_vat);
end;
$$;

create or replace function public.update_product(
  p_id       integer,
  p_name     text    default null,
  p_unit     text    default null,
  p_price    numeric default null,
  p_vat      numeric default null,
  p_category text    default null
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform mobappdata.require_user();
  if not exists (select 1 from mobappdata.products p where p.id = p_id) then
    raise exception 'unknown product %', p_id using errcode = 'P0002';
  end if;
  if num_nulls(p_name, p_unit, p_price, p_vat, p_category) = 5 then
    raise exception 'nothing to change' using errcode = '22023';
  end if;
  return mobappdata.upsert_product(p_id, p_name, p_category, p_unit, p_price, p_vat);
end;
$$;

-- Pictures of the product stay (unassigned).
create or replace function public.delete_product(p_id integer)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform mobappdata.require_user();
  delete from mobappdata.products where id = p_id;
  if not found then
    raise exception 'unknown product %', p_id using errcode = 'P0002';
  end if;
end;
$$;

-- Bulk upsert by id. Shape:
-- { "categories": [ {"id": 13, "name": "Ice Cream"} ],                    (id optional)
--   "products":   [ {"id": 41, "name": "Vanilla", "category": "Ice Cream" | "category_id": 13,
--                    "unit": "PK", "price": 2.49, "vat": 0.07} ] }           (id optional)
-- Unknown category *names* on products are created on the fly.
create or replace function public.import_catalog(p_data jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_item jsonb;
  v_cat  text;
  n_cat  integer := 0;
  n_prod integer := 0;
begin
  perform mobappdata.require_user();

  for v_item in select * from jsonb_array_elements(coalesce(p_data -> 'categories', '[]'::jsonb)) loop
    perform mobappdata.upsert_category(v_item ->> 'name', (v_item ->> 'id')::integer);
    n_cat := n_cat + 1;
  end loop;

  for v_item in select * from jsonb_array_elements(coalesce(p_data -> 'products', '[]'::jsonb)) loop
    v_cat := coalesce(v_item ->> 'category_id', v_item ->> 'category');
    if v_cat is not null and btrim(v_cat) !~ '^\d+$' and lower(btrim(v_cat)) not in ('none', '-')
       and not exists (select 1 from mobappdata.categories c where lower(c.name) = lower(btrim(v_cat))) then
      perform mobappdata.upsert_category(v_cat);
    end if;
    perform mobappdata.upsert_product(
      p_id       => (v_item ->> 'id')::integer,
      p_name     => v_item ->> 'name',
      p_category => v_cat,
      p_unit     => v_item ->> 'unit',
      p_price    => (v_item ->> 'price')::numeric,
      p_vat      => (v_item ->> 'vat')::numeric
    );
    n_prod := n_prod + 1;
  end loop;

  return jsonb_build_object('categories', n_cat, 'products', n_prod);
end;
$$;

-- =============================================================================
-- RPC: pictures
-- =============================================================================

-- Register an object that was just uploaded to the "pictures" bucket.
-- The first picture of a product becomes its primary picture automatically.
create or replace function public.add_picture(
  p_object_path text,
  p_file_name   text,
  p_mime_type   text    default null,
  p_size_bytes  bigint  default null,
  p_title       text    default null,
  p_product_id  integer default null,
  p_is_primary  boolean default null
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid     uuid := mobappdata.require_user();
  v_primary boolean;
  v_id      bigint;
begin
  if not mobappdata.storage_object_exists(p_object_path) then
    raise exception 'object "%" not found in bucket "pictures" (upload first)', p_object_path
      using errcode = 'P0002';
  end if;
  if p_product_id is not null and not exists (select 1 from mobappdata.products p where p.id = p_product_id) then
    raise exception 'unknown product %', p_product_id using errcode = 'P0002';
  end if;

  v_primary := p_product_id is not null and coalesce(
    p_is_primary,
    not exists (select 1 from mobappdata.pictures pi where pi.product_id = p_product_id and pi.is_primary));

  if v_primary then
    update mobappdata.pictures set is_primary = false where product_id = p_product_id and is_primary;
  end if;

  insert into mobappdata.pictures (object_path, file_name, title, mime_type, size_bytes, product_id, is_primary, uploaded_by)
  values (p_object_path, p_file_name, p_title, p_mime_type, p_size_bytes, p_product_id, v_primary, v_uid)
  returning id into v_id;
  return v_id;
end;
$$;

create or replace function public.set_primary_picture(p_picture_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_product_id integer;
begin
  perform mobappdata.require_user();
  select pi.product_id into v_product_id from mobappdata.pictures pi where pi.id = p_picture_id;
  if not found then
    raise exception 'unknown picture %', p_picture_id using errcode = 'P0002';
  end if;
  if v_product_id is null then
    raise exception 'picture % is not attached to a product', p_picture_id using errcode = '22023';
  end if;
  update mobappdata.pictures set is_primary = false where product_id = v_product_id and is_primary;
  update mobappdata.pictures set is_primary = true  where id = p_picture_id;
end;
$$;

-- Delete the metadata row; returns the object path so the caller can delete
-- the binary from storage. Promotes another picture to primary if needed.
create or replace function public.remove_picture(p_picture_id bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_pic mobappdata.pictures;
begin
  perform mobappdata.require_user();
  delete from mobappdata.pictures where id = p_picture_id returning * into v_pic;
  if v_pic.id is null then
    raise exception 'unknown picture %', p_picture_id using errcode = 'P0002';
  end if;
  if v_pic.is_primary then
    update mobappdata.pictures set is_primary = true
     where id = (select min(pi.id) from mobappdata.pictures pi where pi.product_id = v_pic.product_id);
  end if;
  return v_pic.object_path;
end;
$$;

-- =============================================================================
-- RPC: messages + profile
-- =============================================================================

create or replace function public.post_message(p_body text)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid  uuid := mobappdata.require_user();
  v_body text := btrim(p_body, E' \t\r\n');
  v_id   bigint;
begin
  if coalesce(char_length(v_body), 0) = 0 then
    raise exception 'empty message' using errcode = '22023';
  end if;
  if char_length(v_body) > 2000 then
    raise exception 'message longer than 2000 characters' using errcode = '22001';
  end if;
  insert into mobappdata.messages (author_id, body) values (v_uid, v_body) returning id into v_id;
  return v_id;
end;
$$;

-- Edit / delete: only the author. The rule is the `author_id = v_uid` in the
-- WHERE clause — this is the firewall, not RLS (definer functions bypass it).
create or replace function public.edit_message(p_id bigint, p_body text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid  uuid := mobappdata.require_user();
  v_body text := btrim(p_body, E' \t\r\n');
begin
  if coalesce(char_length(v_body), 0) = 0 then
    raise exception 'empty message' using errcode = '22023';
  end if;
  if char_length(v_body) > 2000 then
    raise exception 'message longer than 2000 characters' using errcode = '22001';
  end if;
  update mobappdata.messages
     set body = v_body, edited_at = now()
   where id = p_id
     and author_id = v_uid;                                   -- ← only your own
  if not found then
    raise exception 'message % does not exist or is not yours', p_id using errcode = '42501';
  end if;
end;
$$;

create or replace function public.delete_message(p_id bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := mobappdata.require_user();
begin
  delete from mobappdata.messages
   where id = p_id
     and author_id = v_uid;                                   -- ← only your own
  if not found then
    raise exception 'message % does not exist or is not yours', p_id using errcode = '42501';
  end if;
end;
$$;

create or replace function public.update_my_profile(p_display_name text default null, p_bio text default null)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_name text;
begin
  update mobappdata.profiles
     set display_name = coalesce(nullif(btrim(p_display_name), ''), display_name),
         bio          = coalesce(p_bio, bio)
   where id = mobappdata.require_user()
  returning display_name into v_name;
  return v_name;
end;
$$;
