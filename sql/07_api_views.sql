-- (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
-- =============================================================================
-- 07_api_views.sql — the READ side of the API (schema public)
--
-- These views run with the rights of their OWNER (security_invoker = false,
-- the Postgres default). 09 hands them over to role mobappdata_api, which may
-- read mobappdata although API users can't — and which is subject to RLS.
-- Who may read the views is decided by GRANTs: authenticated + service_role
-- yes, anon no. That is the internal border: data only leaves mobappdata here.
-- =============================================================================

-- --- catalog: plain views (1:1), so apps can embed: products?select=*,category:categories(*)
create view public.categories as
select c.id, c.name
from mobappdata.categories c;

create view public.products as
select p.id, p.name, p.category_id, p.unit, p.price, p.vat
from mobappdata.products p;

-- --- catalog: joined read models ------------------------------------------------
-- price is the shelf price incl. VAT (gross); net + VAT amount are derived.
create view public.product_details as
select p.id, p.name, p.unit, p.price, p.vat,
       trim_scale(p.vat * 100)                     as vat_percent,
       round(p.price / (1 + p.vat), 2)             as price_net,
       p.price - round(p.price / (1 + p.vat), 2)   as vat_amount,
       p.category_id,
       c.name                                      as category,
       coalesce(pics.picture_count, 0)             as picture_count,
       pics.primary_picture_id,
       pics.primary_picture_path
from mobappdata.products p
left join mobappdata.categories c on c.id = p.category_id
left join lateral (
  select count(*)                                         as picture_count,
         max(pi.id)          filter (where pi.is_primary) as primary_picture_id,
         max(pi.object_path) filter (where pi.is_primary) as primary_picture_path
  from mobappdata.pictures pi
  where pi.product_id = p.id
) pics on true;

create view public.category_stats as
select c.id, c.name,
       count(p.id)            as products,
       min(p.price)           as min_price,
       round(avg(p.price), 2) as avg_price,
       max(p.price)           as max_price
from mobappdata.categories c
left join mobappdata.products p on p.category_id = c.id
group by c.id, c.name;

create view public.vat_rates as
select p.vat,
       trim_scale(p.vat * 100)  as vat_percent,
       count(*)                 as products,
       round(avg(p.price), 2)   as avg_price,
       array_agg(distinct c.name order by c.name) filter (where c.name is not null) as categories
from mobappdata.products p
left join mobappdata.categories c on c.id = p.category_id
group by p.vat;

-- --- pictures -----------------------------------------------------------------------
create view public.pictures as
select pi.id, pi.bucket_id, pi.object_path, pi.file_name, pi.title, pi.mime_type, pi.size_bytes,
       pi.is_primary, pi.product_id, p.name as product_name,
       pi.uploaded_by, pr.display_name as uploaded_by_name,
       pi.created_at
from mobappdata.pictures pi
left join mobappdata.products p  on p.id  = pi.product_id
left join mobappdata.profiles pr on pr.id = pi.uploaded_by;

-- --- messages -----------------------------------------------------------------------
-- `mine`: the view runs as its owner, but the caller's JWT is still known.
create view public.messages as
select m.id, m.author_id,
       coalesce(p.display_name, '(deleted user)') as author,
       m.body, m.created_at, m.edited_at,
       m.author_id is not distinct from mobappdata.current_user_id() as mine
from mobappdata.messages m
left join mobappdata.profiles p on p.id = m.author_id;

-- --- users --------------------------------------------------------------------------
create view public.profiles as
select p.id, p.email, p.display_name, p.bio, p.created_at, p.last_sign_in_at,
       (select count(*) from mobappdata.pictures pi where pi.uploaded_by = p.id) as pictures,
       (select count(*) from mobappdata.messages m  where m.author_id    = p.id) as messages,
       (select max(a.at) from mobappdata.user_audit a where a.user_id    = p.id) as last_event_at
from mobappdata.profiles p;

create view public.user_audit as
select a.id, a.user_id, a.email, a.action, a.details, a.at
from mobappdata.user_audit a;
