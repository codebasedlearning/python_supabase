# sbcli — a Supabase control panel for the terminal

`sbcli` manages a Supabase project. That can be the **hosted** one on supabase.com or a **local** stack
running in containers on your machine. Everything is a CLI command:

| Area | What you get |
|---|---|
| **auth** | sign up, log in, log out, whoami (session stored per target) |
| **users** | add / remove / list users, reset passwords, audit log (admin, service-role key) |
| **profile** | your own profile, fed by triggers on `auth.users` |
| **categories / products** | the shop catalog: fuzzy search, filters, VAT breakdown, add/update/remove, import & export (JSON / CSV / SQL) |
| **images** | pictures in the private Storage bucket `pictures` (S3-backed), managed by a pictures table |
| **chat** | one message board: post, read all, edit/delete your own, get notified live (Realtime or polling) |
| **query** | generic, filterable access to every API view |
| **db** | numbered SQL scripts: apply, **reset to zero**, status, raw SQL |
| **local** | start/stop a local Supabase in Docker **or Podman** |

**The design in one sentence:** all data lives in schema **`mobappdata`**, which the API can't
reach, and schema **`public`** holds only **views** (to read) and **RPC functions** (to act). That is
the internal border. Everything is for signed-in users only; anonymous gets nothing (§6).

---

## 1. Install

Requirements: [uv](https://docs.astral.sh/uv/) (`brew install uv`). uv also fetches a suitable Python (≥ 3.10) if needed.

```bash
cd python_supabase
uv run sbcli --help
```

That's all. On the first call `uv run` creates `.venv`, installs the project + dependencies from
`pyproject.toml` and writes `uv.lock`. Every later call checks that the environment matches
the lock file before running. No `venv`, no `source`, no `pip install`.

After editing `pyproject.toml` (new dependency, version bump) there's nothing to do: the next `uv run`
re-syncs. `uv sync` does the same explicitly; `uv lock --upgrade` pulls newer dependency versions.

> Run it inside the project folder. uv finds `pyproject.toml` there, and `sbcli` finds `sql/` and `.env.<target>`.
> From elsewhere: `uv run --project /path/to/python_supabase sbcli …` plus `SBCLI_HOME=/path/to/python_supabase`.

**Too much typing?** Pick one:

```bash
alias sbcli='uv run sbcli'          # in ~/.zshrc, only works inside the project folder
uv tool install --editable .        # puts a global `sbcli` on your PATH, code changes apply live
```

The rest of this README writes `uv run sbcli …`. With one of the above, just drop the `uv run`.

Optional extras: `psql` for `uv run sbcli db psql` (macOS: `brew install libpq && brew link --force libpq`),
shell completion via `sbcli --install-completion` (needs the global tool install).

---

## 2. Targets: `local` and `cloud`

Every command runs against a **target**. Each target has its own config file and its own login session.

| target | config file | how you get it |
|---|---|---|
| `local` | `.env.local` | written by `uv run sbcli local start` / `… local env` |
| `cloud` | `.env.cloud` | `cp .env.example .env.cloud` and paste your keys |

The target is chosen in this order: `-t/--target` → `$SBCLI_TARGET` → `uv run sbcli target <name>` (saved) → `local`.
`-t` can go anywhere: `uv run sbcli products list -t cloud` works.

```bash
uv run sbcli target          # shows both targets, keys masked, who is logged in
uv run sbcli target cloud    # make cloud the default
```

Variables (real environment variables override the file):

| variable | used for |
|---|---|
| `SUPABASE_URL` | API base URL |
| `SUPABASE_ANON_KEY` *(or `SUPABASE_PUBLISHABLE_KEY`)* | everything a user does |
| `SUPABASE_SERVICE_ROLE_KEY` *(or `SUPABASE_SECRET_KEY`)* | `users …` admin commands, bucket purge on reset |
| `DATABASE_URL` | `db …` commands (direct Postgres connection) |

Sessions live in `~/.config/sbcli/sessions/<target>.json` (mode 0600). Access tokens are refreshed
automatically, and refresh tokens rotate.

---

## 3. Local Supabase in containers

`sbcli local` wraps the official **Supabase CLI**. The CLI runs the complete stack (Postgres, Auth,
PostgREST, Realtime, Storage, Studio, Mailpit) as containers.

### 3a. With Docker (Docker Desktop / OrbStack / Colima)

```bash
brew install supabase/tap/supabase       # or: npm i -g supabase   (npx also works)
uv run sbcli local start                        # first run pulls ~2 GB of images, so get a coffee ☕
uv run sbcli db reset -y                        # build schema + your 12 categories / 40 products
uv run sbcli users add me@example.com -n "Me"   # prints a generated password
uv run sbcli auth login me@example.com
uv run sbcli products list
```

`local start` runs `supabase init` if needed (creates `supabase/config.toml`), starts the stack,
writes `.env.local` and sets `local` as the default target. Nothing needs to be exposed besides the
default `public`, which is the point of the design (§6).

### 3b. With Podman

The Supabase CLI accepts any Docker-compatible runtime, so it just needs to be pointed at Podman's socket:

```bash
brew install podman
podman machine init --cpus 4 --memory 8192   # once
podman machine start
export DOCKER_HOST="unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')"

uv run sbcli local start --lean      # --lean skips edge-runtime, logflare, vector, imgproxy
```

`--lean` matters on Podman: the logging containers (`vector`/`logflare`) want to mount the Docker
socket, and that is where Podman setups usually fail. If it still fails, set `[analytics] enabled = false`
in `supabase/config.toml`.

### Useful local URLs

| | |
|---|---|
| API | http://127.0.0.1:54321 |
| Postgres | `postgresql://postgres:postgres@127.0.0.1:54322/postgres` |
| Studio | http://127.0.0.1:54323 (`sbcli local studio`) |
| Mailpit (sign-up mails) | http://127.0.0.1:54324 |

```bash
uv run sbcli local status            # URLs + keys
uv run sbcli local stop              # data survives
uv run sbcli local stop --wipe       # volumes gone, truly from scratch
```

---

## 4. Hosted Supabase (cloud)

1. Create a project on supabase.com.
2. `cp .env.example .env.cloud` and fill in:
   * **URL**: Project Settings → Data API
   * **Keys**: Project Settings → API Keys. The *legacy* `anon` + `service_role` JWT keys are the safest bet.
     The new `sb_publishable_…`/`sb_secret_…` keys should work with current supabase-py; if you get
     `401 / Invalid API key`, switch to the legacy ones.
   * **DATABASE_URL**: the "Connect" button → **Session pooler** (IPv4; the direct connection is IPv6-only).
     URL-encode special characters in the password.
3. Build the schema and create yourself:

```bash
uv run sbcli -t cloud db reset            # asks you to type "cloud" to confirm
uv run sbcli -t cloud users add you@fh-aachen.de -n "You"
uv run sbcli -t cloud auth login you@fh-aachen.de
```

**Do not** add `mobappdata` to Dashboard → Project Settings → Data API → *Exposed schemas*. Only
`public` (the API) belongs there.

Hosted auth has **e-mail confirmation on** by default: `auth signup` users must click the link first,
while `users add` creates them already confirmed.

> 🔐 The service-role / secret key bypasses RLS completely. Keep it in `.env.cloud` (git-ignored)
> and nowhere else.

---

## 5. Commands

`uv run sbcli <group> --help` shows every option. Short tour:

### auth / profile
```bash
uv run sbcli auth signup carol@example.com -n Carol      # self-registration (anon key)
uv run sbcli auth login carol@example.com                # password prompt
uv run sbcli auth whoami
uv run sbcli auth logout [--everywhere]
uv run sbcli profile show                                # profile + last audit events
uv run sbcli profile set --name "Carol C." --bio "likes coffee"
```

### users (admin, needs service-role key)
```bash
uv run sbcli users list [--json]
uv run sbcli users add bob@example.com [-p PASSWORD] [-n Bob]   # confirmed: can log in right away
uv run sbcli users add bob@example.com --require-confirmation    # NOT confirmed: login refused until…
uv run sbcli users confirm bob@example.com                       # …an admin confirms the e-mail
uv run sbcli users remove bob@example.com [-y]           # e-mail or UUID
uv run sbcli users set-password bob@example.com
uv run sbcli users audit [bob@example.com] [-l 50]       # created / signed_in / email_changed / deleted
```

### categories
```bash
uv run sbcli categories list                             # id, name, products, min/avg/max price
uv run sbcli categories add "Ice Cream" [--id 13]        # same name again = no-op
uv run sbcli categories rename "ice cream" "Ice Cream & Sorbet"
uv run sbcli categories remove 13 [--move-to "Frozen Goods"] [-y]   # else products → no category
```
Wherever a category is expected you can give the **id or the name** (case-insensitive), and `none`
means "without category".

### products
```bash
uv run sbcli products list [-s TEXT] [-c CATEGORY] [--min 1] [--max 3] [--vat 0.07] [-u PK] [--json]
uv run sbcli products search piza                        # fuzzy, best match first (finds both pizzas)
uv run sbcli products show 37                            # price = net + VAT, pictures
uv run sbcli products add "Pretzel" -u PC -p 0.89 [--vat 0.07] [-c "baked goods"] [--id 41]
uv run sbcli products update 41 [--name …] [--price 0.95] [--vat …] [-u …] [-c none]   # only what you pass
uv run sbcli products remove 41 [--with-pictures] [-y]
uv run sbcli products vat                                # products per VAT rate
uv run sbcli products import products.csv [--categories categories.csv]   # format: §8
uv run sbcli products import shop.json
uv run sbcli products export shop.json | shop.csv | shop.sql
```
* IDs are identity columns. Without `--id` the next free one is used; explicit IDs (imports, `--id`)
  move the sequence along, so the two never collide.
* Validation in `upsert_product()` gives friendly messages, and CHECK constraints on the table back it up:
  * price ≥ 0
  * VAT is a fraction: `0.07`, not `7`, so `--vat 19` is rejected
  * name ≤ 100 and unit ≤ 20 characters; units are stored upper-case
  * category names are unique (case-insensitive)
* **Price assumption:** `price` is the **gross** shelf price (incl. VAT), so `product_details` derives
  `price_net` and `vat_amount` from it. If yours are net, swap the two formulas in `sql/07_api_views.sql`.
* `export … .sql` writes exactly the layout of `sql/04_catalog_data.sql`, so you can edit the data
  in the DB, export, replace the file, and the next reset brings your version back.

### images
```bash
uv run sbcli images upload front.jpg back.jpg -p 26 [--title "…"] [--primary]   # -p = product id
uv run sbcli images list [-p 26] [--unassigned]
uv run sbcli images download 3 4 -o downloads/
uv run sbcli images url 3 [-e 600]                       # signed URL (bucket is private)
uv run sbcli images primary 4
uv run sbcli images remove 3 [-y]
uv run sbcli images verify [--fix]                       # bucket ↔ table consistency check
```
Objects are stored as `pictures/product-<id>/<uuid>.<ext>` (or `unassigned/…`). Allowed types: jpeg, png,
gif, webp, svg, avif. Max size 10 MB. Both limits are set in `sql/05_pictures.sql`. Deleting a product
keeps its pictures (unassigned) unless you pass `--with-pictures`.

### chat
```bash
uv run sbcli chat send "hello world"
echo "from a pipe" | uv run sbcli chat send -
uv run sbcli chat read [-l 50]                           # latest messages, oldest first, with #id
uv run sbcli chat edit 12 "corrected text"               # only your own messages
uv run sbcli chat delete 12 [-y]                         # only your own messages
uv run sbcli chat listen [-n 5] [--bell]                 # last 5, then live: new ✎ edited ✗ deleted
uv run sbcli chat listen --poll 2                        # same by polling (no websockets needed)
```
* **Rights:** one board for everybody who is signed in. Everyone reads everything; editing or deleting
  someone else's message is refused with `message 12 does not exist or is not yours`.
* **`--bell`** rings the terminal bell on other people's messages. Your own don't count; nobody needs a
  notification for talking to themselves.
* **Polling** sees new and edited messages, but not deletions, because a deleted row simply isn't
  there any more. Realtime sees all three.

### query: any API view, with filters
```bash
uv run sbcli query                                        # list of views
uv run sbcli query product_details -w 'price>2' -w 'category~drinks' -c id,name,price,category -o price.desc
uv run sbcli query category_stats -w 'products=0'         # empty categories
uv run sbcli query messages -w 'body~pizza' --json
```
Operators: `=` `!=` `>` `>=` `<` `<=` `~` (case-insensitive contains) `@` (array contains; `a,b` for several),
and `col=null`. **Quote the expression.** Unquoted, your shell reads `price>2` as "redirect to a file
named 2". (Ask me how I know.)

### db
```bash
uv run sbcli db status                    # scripts applied / pending / changed, row counts, API size
uv run sbcli db apply                     # run scripts not yet applied
uv run sbcli db reset [--wipe-users] [-y]
uv run sbcli db check [-v]                # border test: 34 checks, exit code 1 if any fails (§6)
uv run sbcli db sql "select * from mobappdata.products where price > 3"   # as postgres → no border
uv run sbcli db sql -f some.sql
uv run sbcli db psql
```

---

## 6. Database design: data behind a border

```
          ┌──────────── public  (the API: exposed, signed-in users only) ────────────┐
 client ─►│ VIEWS (read)                         RPC FUNCTIONS (act)                  │
          │ products  categories                 search_products                      │
          │ product_details  category_stats      add_product / update_product /       │
          │ vat_rates  pictures  messages        delete_product                       │
          │ profiles  user_audit                 add_category / rename_category /     │
          │                                      delete_category                      │
          │                                      import_catalog                       │
          │                                      add_picture / remove_picture /       │
          │                                      set_primary_picture                  │
          │                                      post_message / edit_message /        │
          │                                      delete_message   update_my_profile   │
          └────────────┬───────────────────────────────────┬──────────────────────────┘
                       │  all owned by role mobappdata_api: views run as it, SECURITY DEFINER = it
                       │  (exact grants, no BYPASSRLS → RLS applies)       │
          ┌────────────▼────── mobappdata  (data, NOT exposed, no privileges) ───▼────┐
          │ categories ◄── products ◄── pictures ──► storage bucket "pictures"        │
          │ profiles ◄── messages        user_audit        schema_scripts              │
          │ internal: category_id(), upsert_product(), upsert_category(), triggers …  │
          └────────────▲──────────────────────────────────────────────────────────────┘
                       │ triggers
                  auth.users
```

### Three layers

**1. Architecture: the API is the only way in.** `public` holds only views (read) and RPC functions
(act). `mobappdata` is not exposed, and `anon`/`authenticated` have no privileges there. Every write is
an RPC function that states its rules in plain SQL, e.g. `edit_message` updates
`where id = p_id and author_id = v_uid`. Rules spanning several tables ("may only delete a category
if…", later "only within your group") live here, where they are readable, not in policies.

**2. The API role: least privilege.** All views and RPC functions are owned by **`mobappdata_api`**:
* no login, no superuser, no `BYPASSRLS`, not the owner of the tables
* views run with its rights, and `SECURITY DEFINER` means "run as `mobappdata_api`" (not `postgres`)
* it has exactly the grants in `09_security.sql`: the catalog, pictures and messages tables, plus
  read/update on profiles
* it can't drop tables, can't read `auth.users`, and can't write the audit log (only the auth triggers do)

So a bug in a function can only do what the API may do anyway. Two tiny jobs need more than that, and
each runs as `postgres` in a one-line helper that only the API role may call:
* `storage_object_exists()` — "is this file in the bucket?"
* `name_similarity()` — the pg_trgm fuzzy match

**3. RLS: deliberately simple, a real backstop.** Because `mobappdata_api` isn't the table owner, RLS
applies inside the functions. Most tables get one open policy for the API role (`using (true)`): the
functions decide. Only the one-line "it's yours" rules are strict, for messages (insert/update/delete
own) and profiles (update own). So **a function that forgets its `WHERE` is still stopped by the
database**. Verified: the same buggy "update all messages" function changes 2 rows when owned by
`postgres` and only the caller's own row when owned by `mobappdata_api`.

Rule of thumb for new functions:
* the first line is `mobappdata.require_user()`
* every `UPDATE`/`DELETE` on user-owned rows carries the ownership check in its `WHERE` and raises if
  `not found`
* a new table needs grants for `mobappdata_api` plus a policy (§7)
* run `sbcli db check` afterwards

### Other details
* **No writes through views:** simple views would be auto-updatable in Postgres, but users only get
  `SELECT` on them, so `INSERT`/`UPDATE`/`DELETE` fails with `permission denied`.
* **Functions inside views run as the caller** (a Postgres rule). That's why `authenticated` may
  execute `mobappdata.current_user_id()`, used for the `mine` column. It only returns the caller's own id.
* **`current_user_id()` instead of `auth.uid()`:** same logic (the `sub` claim of the request JWT), but
  in our own schema, so the API role needs no rights in Supabase's `auth` schema.
* **One deliberate exception:** `authenticated` may `SELECT` `mobappdata.messages`. Supabase Realtime
  only delivers a row to subscribers allowed to read the underlying table. It's read-only and the same
  data as the `messages` view, so the border loses nothing.
* **Storage** is the other place users act directly, because the Storage API runs as the user. Hence
  the policies on `storage.objects` for the private `pictures` bucket.

### Proof: `sbcli db check`

`uv run sbcli db check` creates two temporary users and runs 34 checks:
* **anon:** can't read views or call functions
* **border:** views readable, embedding works, no writes through views, `mobappdata` unreachable
* **rules:** VAT / price validation; B can't edit or delete A's message; `mine` per user; profile
  updates stay with their owner
* **structure** (needs `DATABASE_URL`): no tables in `public`, all API objects owned by
  `mobappdata_api`, definer + fixed `search_path`, role attributes, RLS everywhere, privileges of
  anon/authenticated
* **defence:** a buggy update without owner check, run as the API role, changes 0 rows; the API
  role can't write the audit log, read `auth.users` or drop tables

It then removes the test users and exits with code 1 if anything fails, so it works in CI as well.
The check itself is verified: opening four holes on purpose (anon grant on a view, one function back to
`postgres`, a loosened policy) makes exactly those checks fail. Requires the service-role/secret key.
The test users leave entries in the append-only `user_audit`; that's by design.

> Supabase's *Security Advisor* flags views without `security_invoker` ("security definer view").
> Here that's intentional: the views run as the restricted `mobappdata_api`, which is exactly what
> makes the border work.

> ⚠️ If your app should show the catalog **without login**, grant `anon` select on the catalog views
> only: `grant select on public.products, public.categories, public.product_details to anon;`.
> Add it to `09_security.sql` so it survives resets.

### Tables (`mobappdata`)

| table | notes |
|---|---|
| `categories` | `id` (identity), `name` (unique, case-insensitive) |
| `products` | `id` (identity), `name`, `category_id` (nullable FK), `unit`, `price` (gross), `vat` (fraction), CHECKs, trigram index on name |
| `pictures` | metadata of objects in bucket `pictures`, optional product link (`on delete set null`), one primary per product |
| `messages` | `author_id` (→ profiles, `set null` on user deletion), `body` (1–2000 chars), `created_at`, `edited_at` |
| `profiles` | 1:1 with `auth.users`, `on delete cascade` |
| `user_audit` | append-only: `created`, `signed_in`, `email_changed`, `deleted` (no FK, survives deletion) |
| `schema_scripts` | which numbered scripts ran |

Triggers on `auth.users` (insert / update / delete) feed `profiles` and `user_audit`. They are
`SECURITY DEFINER` functions in `mobappdata`, so the auth service needs no privileges there.

### API views (`public`)

| view | content |
|---|---|
| `products`, `categories` | 1:1, plain columns, embeddable (`category:categories(*)`) |
| `product_details` | product + `category` name, `vat_percent`, `price_net`, `vat_amount`, picture count / primary picture |
| `category_stats` | category + `products`, `min/avg/max_price` |
| `vat_rates` | per VAT rate: products, avg price, categories |
| `pictures` | picture + `product_name`, `uploaded_by_name` |
| `messages` | message + `author` name, `edited_at`, `mine` (= written by the caller) |
| `profiles` | users + counters (pictures, messages, last event) |
| `user_audit` | lifecycle log |

### API functions (`public`, RPC)

| function | purpose |
|---|---|
| `search_products(p_query, p_category, p_min_price, p_max_price, p_vat, p_unit, p_limit, p_offset)` | substring + typo-tolerant search (`word_similarity`), filters → rows of `product_details` |
| `add_product(p_name, p_unit, p_price, p_vat, p_category, p_id)` | create (error if `p_id` exists) |
| `update_product(p_id, p_name, p_unit, p_price, p_vat, p_category)` | only non-NULL fields, category `none` clears |
| `delete_product(p_id)` | pictures become unassigned |
| `add_category(p_name, p_id)`, `rename_category(p_category, p_name)`, `delete_category(p_category, p_move_to)` | categories (id or name everywhere) |
| `import_catalog(p_data jsonb)` | bulk upsert by id; unknown category names are created |
| `add_picture(…)`, `remove_picture(p_picture_id)`, `set_primary_picture(p_picture_id)` | picture bookkeeping (first picture becomes primary) |
| `post_message(p_body)` | post (trimmed, 1–2000 chars) |
| `edit_message(p_id, p_body)`, `delete_message(p_id)` | only the author; sets `edited_at` |
| `update_my_profile(p_display_name, p_bio)` | own profile only |

From an app, for example in Python:
`supabase.rpc("add_product", {"p_name": "Pretzel", "p_unit": "PC", "p_price": 0.89, "p_vat": 0.07}).execute()`.
Reading is just as plain: `supabase.table("product_details").select("*").eq("category", "Snacks").execute()`.

---

## 7. The numbered scripts: erase everything, start over

```
sql/
  00_reset.sql          DROP schema mobappdata (cascade → also the auth.users triggers),
                        DROP + recreate schema public, storage policies, bucket, role mobappdata_api
  01_foundation.sql     role mobappdata_api, schema mobappdata, extensions (pg_trgm), helpers
  02_profiles.sql       profiles + user_audit + auth.users triggers + back-fill of existing users
  03_catalog.sql        categories + products (tables, constraints, indexes)
  04_catalog_data.sql   the data: 12 categories, 40 products (course data)
  05_pictures.sql       bucket "pictures", storage policies, pictures table
  06_messages.sql       messages table
  07_api_views.sql      the read side of the API
  08_api_functions.sql  the action side of the API + internal helpers
  09_security.sql       the border: grants for mobappdata_api, RLS policies, revokes
  10_realtime.sql       publish mobappdata.messages for Realtime
  99_api_handover.sql   ALWAYS LAST: every view/function in public → owned by mobappdata_api,
                        SELECT/EXECUTE for signed-in users only (re-run after every apply/reset)
  extra/wipe_auth_users.sql   only with --wipe-users
```

* `uv run sbcli db reset` = purge bucket via Storage API → `00` → (`wipe_auth_users`) → `01…10` → `99` → reload PostgREST schema.
* Each script runs in **its own transaction**. If one fails, it is rolled back and the run stops there.
* Nothing outside `mobappdata`, `public`, the `pictures` bucket and the triggers on `auth.users` is
  touched. Other schemas in the project (e.g. an older `ami_zone`) stay as they are.
* `db apply` only runs scripts not applied yet, and warns if an applied script has changed since
  (then `reset`). It's deliberately not a migration framework: during development, *edit the script and reset*.
* Users survive a reset by default. `02` back-fills their profiles, so the data stays consistent.
* Want your own additions? Add `11_whatever.sql`, `12_…`; scripts run in numeric order, then `99`.
  * new table → `mobappdata`, plus grant and policy for `mobappdata_api` (in your script or in 09)
  * something to read → view in `public`
  * something to do → RPC (`security definer`, `set search_path = ''`) in `public`

  You don't need to care about ownership: `99_api_handover.sql` runs after every `db apply` and hands
  every new view and function to `mobappdata_api`. Then run `db check`. (Tested: a later `11_…` with a
  new view + function → `db apply` → owned by the API role, check green.)
* Plain psql works too. Use `-v ON_ERROR_STOP=1 -1`, otherwise psql carries on after an error:
  `for f in sql/[0-9]*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$f" || break; done`
  (the glob sorts `99_…` last, as it should be)

Why does the bucket go through the API? Hosted Supabase forbids direct `DELETE` on `storage.objects`.
`00_reset.sql` tries anyway, but only as a fallback that warns instead of failing.

---

## 8. Import / export format

**JSON** (also what `products export x.json` writes; `id` is optional everywhere, so missing means a new id):
```json
{
  "categories": [ {"id": 13, "name": "Ice Cream"} ],
  "products": [
    {"id": 41, "name": "Vanilla Ice Cream", "category": "Ice Cream", "unit": "PK", "price": 2.49, "vat": 0.07},
    {"name": "Club Mate", "category_id": 5, "unit": "PC", "price": 1.60, "vat": 0.19}
  ]
}
```
**CSV** (delimiter `,` `;` or tab is detected; decimal comma OK, e.g. `2,49`).
Columns are `id,name,category,unit,price,vat`, where `category` is a name or id (`category_id` works too).
An empty `id` means a new product.

```text
id;name;category;unit;price;vat
41;Vanilla Ice Cream;Ice Cream;PK;2,49;0,07
42;Mango Sorbet;Ice Cream;PK;2,99;0,07
;Club Mate;Cold Drinks;PC;1,60;0,19
```

Categories can go in a separate CSV passed with `--categories`:

```text
id,name
13,Ice Cream
14,Household
```

* Imports are **upserts by id**: an existing id is overwritten, so running one twice is harmless.
* A category *name* that doesn't exist yet is created on the fly.
* Products are sent in batches of 500.

**SQL** (`products export shop.sql`): same layout as `sql/04_catalog_data.sql` (inserts with explicit ids
+ `select mobappdata.sync_id_sequences();`).

---

## 9. Troubleshooting

| symptom | fix |
|---|---|
| `Not logged in on target …` | `sbcli auth login EMAIL` (sessions are per target) |
| `permission denied for view/function … [42501]` | not logged in, or calling with the anon key |
| `Could not find the function … [PGRST202]` | schema missing → `sbcli db status`, `sbcli db reset` |
| `The schema must be one of the following: public [PGRST106]` | someone tried `mobappdata` directly; that's the border working |
| `Missing configuration …` | the message names the variable and the file |
| `Database error saving new user` (auth) | triggers broken/outdated → `sbcli db reset` |
| `vat is a fraction (0.07 = 7 %) …` | you typed `--vat 19`; it's `--vat 0.19` |
| `unknown category "…"` | check `categories list` |
| `object … not found in bucket "pictures"` on upload | storage and DB disagree → `images verify`; on hosted, check that role `postgres` may read `storage.objects` |
| `permission denied for function …` / `… for table …` inside an RPC | a new object without grant/policy for `mobappdata_api` → `09_security.sql`, then `db check` |
| `db check` reports ✗ | `-v` shows what was allowed that shouldn't be (or vice versa) |
| `Realtime not reachable` | stack without realtime? use `chat listen --poll 2` |
| cloud `db` commands time out | use the **Session pooler** URL, not the IPv6 direct host |

---

## 10. Project layout

```
python_supabase/
├── pyproject.toml          # entry point: sbcli = sbcli.cli:main
├── .env.example            # template for .env.cloud
├── sql/                    # numbered setup scripts (see §7)
├── supabase/               # created by `sbcli local init` (Supabase CLI config)
└── src/sbcli/
    ├── cli.py              # Typer app, error handling, `target`
    ├── config.py           # targets, .env loading, paths
    ├── context.py          # anon / user / admin clients, session refresh
    ├── session.py          # token storage
    ├── output.py           # rich tables / JSON
    └── commands/           # auth, users, catalog, images, chat, query, db, local
```

Built on [supabase-py](https://github.com/supabase/supabase-py), [Typer](https://typer.tiangolo.com),
[Rich](https://rich.readthedocs.io) and [psycopg 3](https://www.psycopg.org/psycopg3/).
