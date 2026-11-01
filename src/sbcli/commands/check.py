# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""db check — prove the border holds: what must work works, what must fail fails.

Creates two temporary users (admin API), runs the checks through the real
API (anon / user A / user B), inspects the structure in the database
(DATABASE_URL), and removes the users again. Exit code 1 if anything fails.
"""

from __future__ import annotations

import json
import secrets
from typing import Callable

import typer
from postgrest.exceptions import APIError
from supabase import ClientOptions, create_client

from sbcli import context
from sbcli.errors import CliError
from sbcli.output import console, info, table

API_ROLE = "mobappdata_api"


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, group: str, name: str, passed: bool, detail: str = "") -> None:
        self.rows.append({"group": group, "check": name, "result": "[green]✓[/green]" if passed else "[red]✗ FAIL[/red]",
                          "detail": detail, "_ok": passed})

    def denied(self, group: str, name: str, fn: Callable) -> None:
        """Passes if the call is refused by the database/API."""
        try:
            fn()
        except APIError as exc:
            self.add(group, name, True, f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')}"[:70])
            return
        except Exception as exc:  # network etc. → that's not a "denied", that's a broken check
            self.add(group, name, False, f"unexpected error: {exc}"[:70])
            return
        self.add(group, name, False, "was ALLOWED")

    def works(self, group: str, name: str, fn: Callable, expect: Callable | None = None) -> object:
        try:
            result = fn()
        except Exception as exc:
            self.add(group, name, False, f"{getattr(exc, 'message', exc)}"[:70])
            return None
        if expect is not None and not expect(result):
            self.add(group, name, False, f"unexpected result: {str(result)[:50]}")
            return result
        self.add(group, name, True)
        return result

    @property
    def failed(self) -> int:
        return sum(not r["_ok"] for r in self.rows)


def _client(url: str, key: str):
    return create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))


def check_cmd(ctx: typer.Context, verbose: bool = typer.Option(False, "--verbose", "-v", help="Show details of passed checks.")):
    """Border test: anon gets nothing, users only via views/RPC, own-only rules hold, structure is sound."""
    c = context.get(ctx)
    s = c.settings
    s.require("url", "anon_key")
    if not s.service_key:
        raise CliError("db check needs the service-role/secret key (it creates two temporary test users).")
    admin = c.admin()
    rep = Report()
    tag = secrets.token_hex(3)
    users: dict[str, dict] = {}

    try:
        # --- setup: two throw-away users -------------------------------------------------
        for who in ("a", "b"):
            email, pw = f"sbcli-check-{tag}-{who}@example.com", secrets.token_urlsafe(16)
            u = admin.auth.admin.create_user({"email": email, "password": pw, "email_confirm": True,
                                              "user_metadata": {"display_name": f"check-{who.upper()}"}}).user
            cl = _client(s.url, s.anon_key)
            cl.auth.sign_in_with_password({"email": email, "password": pw})
            users[who] = {"id": u.id, "email": email, "client": cl}
        ua, ub = users["a"]["client"], users["b"]["client"]
        anon = _client(s.url, s.anon_key)
        info(f"temporary users: {users['a']['email']}, {users['b']['email']}")

        # --- 1. anonymous: nothing ---------------------------------------------------------
        g = "anon"
        rep.denied(g, "read view products", lambda: anon.table("products").select("id").limit(1).execute())
        rep.denied(g, "read view messages", lambda: anon.table("messages").select("id").limit(1).execute())
        rep.denied(g, "call search_products", lambda: anon.rpc("search_products", {}).execute())
        rep.denied(g, "call post_message", lambda: anon.rpc("post_message", {"p_body": "x"}).execute())

        # --- 2. signed in: views + RPC only --------------------------------------------------
        g = "border"
        rep.works(g, "read view products", lambda: ua.table("products").select("id").limit(1).execute().data,
                  expect=lambda d: len(d) == 1)
        rep.works(g, "embed categories in products (app style)",
                  lambda: ua.table("products").select("id,category:categories(name)").limit(1).execute().data,
                  expect=lambda d: d and "category" in d[0])
        rep.denied(g, "INSERT through view products",
                   lambda: ua.table("products").insert({"name": "x", "unit": "PC", "price": 1}).execute())
        rep.denied(g, "UPDATE through view products",
                   lambda: ua.table("products").update({"price": 0}).eq("id", 1).execute())
        rep.denied(g, "DELETE through view messages", lambda: ua.table("messages").delete().gt("id", 0).execute())
        rep.denied(g, "reach schema mobappdata directly",
                   lambda: ua.schema("mobappdata").table("products").select("id").limit(1).execute())

        # --- 3. business rules in the RPC layer ----------------------------------------------
        g = "rules"
        rep.denied(g, "add_product with vat 19 (not a fraction)",
                   lambda: ua.rpc("add_product", {"p_name": "x", "p_unit": "PC", "p_price": 1, "p_vat": 19}).execute())
        rep.denied(g, "add_product with negative price",
                   lambda: ua.rpc("add_product", {"p_name": "x", "p_unit": "PC", "p_price": -1}).execute())
        mid = rep.works(g, "A posts a message", lambda: ua.rpc("post_message", {"p_body": "check"}).execute().data)
        mid2 = ua.rpc("post_message", {"p_body": "check 2"}).execute().data
        if mid:
            rep.denied(g, "B edits A's message", lambda: ub.rpc("edit_message", {"p_id": mid, "p_body": "hacked"}).execute())
            rep.denied(g, "B deletes A's message", lambda: ub.rpc("delete_message", {"p_id": mid}).execute())
            rep.works(g, "A's message unchanged",
                      lambda: ua.table("messages").select("body").eq("id", mid).execute().data,
                      expect=lambda d: d and d[0]["body"] == "check")
            rep.works(g, "`mine` is true for A", lambda: ua.table("messages").select("mine").eq("id", mid).execute().data,
                      expect=lambda d: d and d[0]["mine"] is True)
            rep.works(g, "`mine` is false for B", lambda: ub.table("messages").select("mine").eq("id", mid).execute().data,
                      expect=lambda d: d and d[0]["mine"] is False)
            rep.works(g, "A edits own message", lambda: ua.rpc("edit_message", {"p_id": mid, "p_body": "edited"}).execute())
            rep.works(g, "A deletes own message", lambda: ua.rpc("delete_message", {"p_id": mid}).execute())
        before = ua.table("profiles").select("display_name").eq("id", users["a"]["id"]).execute().data
        ub.rpc("update_my_profile", {"p_display_name": "renamed-by-B"}).execute()
        rep.works(g, "B's profile update leaves A's profile alone",
                  lambda: ua.table("profiles").select("display_name").eq("id", users["a"]["id"]).execute().data,
                  expect=lambda d: d == before)

        # --- 4. structure + defence in depth (needs DATABASE_URL) ----------------------------
        if s.db_url:
            _db_checks(c, rep, users, mid2)
        else:
            info("DATABASE_URL not set → structural checks skipped")
    finally:
        # --- cleanup ------------------------------------------------------------------------
        for u in users.values():
            try:
                for m in u["client"].table("messages").select("id").eq("mine", True).execute().data:
                    u["client"].rpc("delete_message", {"p_id": m["id"]}).execute()
            except Exception:
                pass
            try:
                admin.auth.admin.delete_user(u["id"])
            except Exception as exc:
                console.print(f"[yellow]could not delete test user {u['email']}: {exc}[/yellow]")

    cols = ["group", "check", "result"] + (["detail"] if verbose else [])
    table(rep.rows, cols, title=f"border check on '{c.target}'")
    if rep.failed:
        console.print(f"[bold red]{rep.failed} check(s) FAILED[/bold red]  (run with -v for details)")
        raise typer.Exit(1)
    console.print(f"[bold green]all {len(rep.rows)} checks passed[/bold green] — the border holds.")


def _db_checks(c, rep: Report, users: dict, msg_id: int | None) -> None:
    from sbcli.commands.db import connect  # local import: db imports nothing from here

    g = "structure"
    with connect(c) as conn, conn.cursor() as cur:
        def one(sql: str, params=None):
            cur.execute(sql, params)
            return cur.fetchone()[0]

        n = one("select count(*) from pg_class where relnamespace = 'public'::regnamespace and relkind in ('r','p')")
        rep.add(g, "no tables in public (API only)", n == 0, f"{n} table(s)")

        n = one("select count(*) from pg_class where relnamespace = 'public'::regnamespace and relkind = 'v' "
                "and pg_get_userbyid(relowner) <> %s", (API_ROLE,))
        rep.add(g, f"all API views owned by {API_ROLE}", n == 0, f"{n} other")

        n = one("select count(*) from pg_proc where pronamespace = 'public'::regnamespace "
                "and pg_get_userbyid(proowner) <> %s", (API_ROLE,))
        rep.add(g, f"all API functions owned by {API_ROLE}", n == 0, f"{n} other")

        bad = one("select string_agg(proname, ', ') from pg_proc where pronamespace = 'public'::regnamespace "
                  "and (not prosecdef or not exists (select 1 from unnest(coalesce(proconfig, '{}')) x "
                  "where x like 'search_path=%%'))")
        rep.add(g, "API functions: SECURITY DEFINER + fixed search_path", bad is None, bad or "")

        row = cur.execute("select rolsuper, rolbypassrls, rolcanlogin, has_schema_privilege(%s, 'public', 'CREATE') "
                          "from pg_roles where rolname = %s", (API_ROLE, API_ROLE)).fetchone()
        rep.add(g, f"{API_ROLE}: no superuser/bypassrls/login/create", row is not None and not any(row), str(row))

        bad = one("select string_agg(relname, ', ') from pg_class where relnamespace = 'mobappdata'::regnamespace "
                  "and relkind = 'r' and not relrowsecurity")
        rep.add(g, "RLS enabled on every mobappdata table", bad is None, bad or "")

        bad = one("select string_agg(c.relname, ', ') from pg_class c where c.relnamespace = 'public'::regnamespace "
                  "and c.relkind = 'v' and has_table_privilege('anon', c.oid, 'SELECT')")
        rep.add(g, "anon: no SELECT on any view", bad is None, bad or "")

        bad = one("select string_agg(p.proname, ', ') from pg_proc p where p.pronamespace = 'public'::regnamespace "
                  "and has_function_privilege('anon', p.oid, 'EXECUTE')")
        rep.add(g, "anon: no EXECUTE on any function", bad is None, bad or "")

        bad = one("select string_agg(c.relname || ':' || priv, ', ') from pg_class c "
                  "cross join unnest(array['SELECT','INSERT','UPDATE','DELETE']) priv "
                  "where c.relnamespace = 'mobappdata'::regnamespace and c.relkind = 'r' "
                  "and has_table_privilege('authenticated', c.oid, priv) "
                  "and not (c.relname = 'messages' and priv = 'SELECT')")
        rep.add(g, "authenticated: nothing in mobappdata (but SELECT messages)", bad is None, bad or "")

        # defence in depth: a function that FORGETS the ownership check, run as the API role
        g = "defence"
        if msg_id:
            claims = json.dumps({"sub": users["b"]["id"], "role": "authenticated"})
            with conn.transaction(force_rollback=True):
                cur.execute(f"set local role {API_ROLE}")
                cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
                cur.execute("update mobappdata.messages set body = 'HACKED' where id = %s", (msg_id,))
                rep.add(g, "RLS stops a buggy 'update without owner check'", cur.rowcount == 0,
                        f"{cur.rowcount} row(s) changed")
        for name, sql in (("API role can't write the audit log",
                           "insert into mobappdata.user_audit (user_id, action) values (gen_random_uuid(), 'created')"),
                          ("API role can't read auth.users", "select count(*) from auth.users"),
                          ("API role can't drop tables", "drop table mobappdata.products")):
            try:
                with conn.transaction(force_rollback=True):
                    cur.execute(f"set local role {API_ROLE}")
                    cur.execute(sql)
                rep.add(g, name, False, "was ALLOWED")
            except Exception as exc:
                rep.add(g, name, True, str(exc).splitlines()[0][:70])
