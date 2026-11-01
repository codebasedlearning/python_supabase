# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""db: run the numbered SQL scripts (direct Postgres connection via DATABASE_URL)."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
import typer

from sbcli import context
from sbcli.config import BUCKET, mask, sql_dir
from sbcli.errors import CliError
from sbcli.output import console, info, ok, table, warn

db_app = typer.Typer(help="Schema scripts: apply, reset, status, border check, raw SQL.", no_args_is_help=True)

_SCRIPT_RE = re.compile(r"^(\d{2})_[\w-]+\.sql$")


@dataclass
class Script:
    number: int
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()[:16]


def scripts() -> list[Script]:
    out = []
    for p in sorted(sql_dir().iterdir()):
        m = _SCRIPT_RE.match(p.name)
        if m:
            out.append(Script(int(m.group(1)), p))
    return out


def reset_script() -> Script:
    return next(s for s in scripts() if s.number == 0)


FINALIZER = 99   # 99_*.sql: re-run after every apply/reset (hands new API objects to the API role)


def setup_scripts() -> list[Script]:
    return [s for s in scripts() if 1 <= s.number < FINALIZER]


def finalizer_scripts() -> list[Script]:
    return [s for s in scripts() if s.number == FINALIZER]


def connect(c) -> psycopg.Connection:
    c.settings.require("db_url")
    try:
        # autocommit: every script gets its own explicit transaction (see run_script)
        conn = psycopg.connect(c.settings.db_url, connect_timeout=10, autocommit=True)
    except psycopg.OperationalError as exc:
        raise CliError(f"Cannot connect to {mask(c.settings.db_url)}:\n{exc}") from exc
    conn.add_notice_handler(
        lambda d: warn(d.message_primary) if d.severity in ("WARNING",) else None
    )
    return conn


def applied(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("select to_regclass('mobappdata.schema_scripts') is not null")
        if not cur.fetchone()[0]:
            return {}
        cur.execute("select file_name, checksum from mobappdata.schema_scripts")
        return dict(cur.fetchall())


def run_script(conn, script: Script, record: bool = True, settings: dict | None = None) -> None:
    """One script = one transaction. `settings` become transaction-local GUCs (set_config)."""
    sql = script.path.read_text(encoding="utf-8")
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                for key, value in (settings or {}).items():
                    cur.execute("select set_config(%s, %s, true)", (key, value))
                cur.execute(sql)
                if record and script.number > 0:
                    cur.execute(
                        "insert into mobappdata.schema_scripts (file_name, checksum) values (%s, %s) "
                        "on conflict (file_name) do update set checksum = excluded.checksum, applied_at = now()",
                        (script.name, script.checksum),
                    )
    except psycopg.Error as exc:
        diag = getattr(exc, "diag", None)
        where = f"\n  {diag.context}" if diag and diag.context else ""
        raise CliError(f"{script.name} failed: {str(exc).strip()}{where}\n(transaction rolled back)") from exc
    ok(script.name)


def _apply(conn, force: bool = False) -> int:
    done = applied(conn)
    count = 0
    for s in setup_scripts():
        if s.name in done and not force:
            if done[s.name] != s.checksum:
                warn(f"{s.name} changed since it was applied — run `sbcli db reset` to rebuild")
            else:
                info(f"  {s.name} already applied")
            continue
        run_script(conn, s)
        count += 1
    # finalizers: always after anything changed (or if they themselves are new/changed)
    for s in finalizer_scripts():
        if count or force or done.get(s.name) != s.checksum:
            run_script(conn, s)
            count += 1
    if count:
        reload_api_schema(conn)
    return count


def reload_api_schema(conn) -> None:
    """Tell PostgREST to re-read the schema (new tables / functions visible at once)."""
    conn.execute("notify pgrst, 'reload schema'")


def _purge_bucket(c) -> None:
    """Hosted Supabase forbids DELETE on storage tables → use the Storage API."""
    if not c.settings.service_key:
        warn("No service-role key: cannot empty the storage bucket via API (SQL fallback will try).")
        return
    try:
        storage = c.admin().storage
        storage.empty_bucket(BUCKET)
        storage.delete_bucket(BUCKET)
        ok(f"storage bucket '{BUCKET}' emptied and deleted")
    except Exception as exc:  # bucket missing on first run — fine
        info(f"  bucket purge skipped: {exc}")


@db_app.command("apply")
def apply_cmd(ctx: typer.Context):
    """Run all numbered scripts (01, 02, …) that haven't been applied yet."""
    c = context.get(ctx)
    with connect(c) as conn:
        n = _apply(conn)
    ok(f"{n} script(s) applied on '{c.target}'.")


@db_app.command("reset")
def reset_cmd(
    ctx: typer.Context,
    wipe_users: bool = typer.Option(False, "--wipe-users", help="Also delete ALL auth users."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
):
    """DROP everything (00_reset.sql) and rebuild from 01… — the catalog data comes back from 04."""
    c = context.get(ctx)
    c.settings.require("db_url")
    if not yes:
        console.print(f"[bold red]This erases the schemas 'mobappdata' (all data) and 'public' (the API) "
                      f"and the '{BUCKET}' bucket on '{c.target}'[/bold red] ({mask(c.settings.db_url)})"
                      + ("\n[bold red]…and ALL auth users.[/bold red]" if wipe_users else ""))
        answer = typer.prompt(f"Type the target name ({c.target}) to confirm")
        if answer.strip() != c.target:
            raise typer.Abort()
    _purge_bucket(c)
    with connect(c) as conn:
        run_script(conn, reset_script(), record=False)
        if wipe_users:
            run_script(conn, Script(-1, sql_dir() / "extra" / "wipe_auth_users.sql"), record=False)
        _apply(conn, force=True)
    ok(f"Database on '{c.target}' rebuilt from scratch.")
    if wipe_users:
        info("All users are gone — your local session is now meaningless: sbcli auth logout")


@db_app.command("status")
def status_cmd(ctx: typer.Context):
    """Applied scripts, row counts of the data tables, size of the API."""
    c = context.get(ctx)
    with connect(c) as conn:
        done = applied(conn)
        rows = []
        for s in scripts():
            if s.number == 0:
                state = "reset script"
            elif s.name not in done:
                state = "[yellow]pending[/yellow]"
            elif done[s.name] != s.checksum:
                state = "[red]changed[/red]"
            else:
                state = "[green]applied[/green]"
            rows.append({"script": s.name, "state": state})
        info(f"target '{c.target}' → {mask(c.settings.db_url)}")
        table(rows, ["script", "state"], title="scripts")
        with conn.cursor() as cur:
            cur.execute(
                "select c.relname, c.relrowsecurity from pg_class c "
                "join pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind = 'r' and n.nspname = 'mobappdata' order by 1"
            )
            counts = []
            for name, rls in cur.fetchall():
                cur.execute(f'select count(*) from mobappdata."{name}"')
                counts.append({"table": f"mobappdata.{name}", "rows": cur.fetchone()[0], "rls": rls})
            cur.execute(
                "select (select count(*) from pg_class c where c.relnamespace = 'public'::regnamespace"
                "        and c.relkind = 'v'),"
                "       (select count(*) from pg_class c where c.relnamespace = 'public'::regnamespace"
                "        and c.relkind in ('r', 'p')),"
                "       (select count(*) from pg_proc p where p.pronamespace = 'public'::regnamespace)"
                " where to_regnamespace('public') is not null"
            )
            api = cur.fetchone()
        table(counts, ["table", "rows", "rls"], title="data (mobappdata)", right=["rows"])
        if api:
            views, tables, funcs = api
            info(f"API (public): {views} views, {funcs} RPC functions"
                 + (f" — [yellow]{tables} table(s) in public, should be 0[/yellow]" if tables else ""))


@db_app.command("sql")
def sql_cmd(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(None, help="SQL to run (as postgres!)."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", exists=True, dir_okay=False),
):
    """Run raw SQL as the database owner (bypasses RLS — handle with care)."""
    if not query and not file:
        raise CliError("Give a query or --file.")
    sql = file.read_text(encoding="utf-8") if file else query
    c = context.get(ctx)
    with connect(c) as conn, conn.cursor() as cur:
        try:
            cur.execute(sql)
        except psycopg.Error as exc:
            raise CliError(str(exc).strip()) from exc
        if cur.description:
            cols = [d.name for d in cur.description]
            table([dict(zip(cols, r)) for r in cur.fetchall()], cols)
        else:
            ok(cur.statusmessage or "done")
        conn.commit()


@db_app.command("psql")
def psql_cmd(ctx: typer.Context):
    """Open psql on the target database (needs psql installed)."""
    c = context.get(ctx)
    c.settings.require("db_url")
    if not shutil.which("psql"):
        raise CliError("psql not found (macOS: brew install libpq && brew link --force libpq)")
    raise typer.Exit(subprocess.call(["psql", c.settings.db_url], env=os.environ.copy()))
