# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""Entry point: `sbcli` — assembles all command groups and handles errors."""

from __future__ import annotations

import sys
from typing import Optional

import httpx
import psycopg
import typer
from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError
from supabase_auth.errors import AuthError

from sbcli import __version__, session
from sbcli.commands.auth import auth_app, profile_app
from sbcli.commands.catalog import categories_app, products_app
from sbcli.commands.chat import chat_app
from sbcli.commands.check import check_cmd
from sbcli.commands.db import db_app
from sbcli.commands.images import images_app
from sbcli.commands.local import local_app
from sbcli.commands.query import query
from sbcli.commands.users import users_app
from sbcli.config import TARGETS, Settings, mask, resolve_target, write_state
from sbcli.context import Ctx
from sbcli.errors import CliError
from sbcli.output import console, err_console, ok, table

app = typer.Typer(
    help="Manage a Supabase project — local (containers) or hosted — from the terminal.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version(value: bool):
    if value:
        console.print(f"sbcli {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    target: Optional[str] = typer.Option(None, "--target", "-t",
                                         help="local | cloud  (default: $SBCLI_TARGET, then `sbcli target`, then local)"),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
):
    ctx.obj = Ctx(target=resolve_target(target))


@app.command("target")
def target_cmd(
    ctx: typer.Context,
    name: Optional[str] = typer.Argument(None, help="Set the default target: local | cloud"),
):
    """Show configuration of all targets, or set the default one."""
    if name:
        if name not in TARGETS:
            raise CliError(f"Unknown target '{name}'. Use one of: {', '.join(TARGETS)}")
        write_state(target=name)
        ok(f"Default target is now '{name}'.")
    current = resolve_target(None)
    rows = []
    for t in TARGETS:
        s = Settings.load(t)
        sess = session.load(t) or {}
        rows.append({
            "target": ("➜ " if t == current else "  ") + t,
            "env file": s.env_file.name + ("" if s.env_file.exists() else " (missing)"),
            "url": s.url or "—",
            "anon key": mask(s.anon_key),
            "service key": mask(s.service_key),
            "database": mask(s.db_url),
            "logged in": sess.get("email") or "—",
        })
    table(rows, title="targets")


app.command("query")(query)
app.add_typer(auth_app, name="auth")
app.add_typer(profile_app, name="profile")
app.add_typer(users_app, name="users")
app.add_typer(categories_app, name="categories")
app.add_typer(products_app, name="products")
app.add_typer(images_app, name="images")
app.add_typer(chat_app, name="chat")
db_app.command("check")(check_cmd)
app.add_typer(db_app, name="db")
app.add_typer(local_app, name="local")


def _explain_api_error(exc: APIError) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    code = getattr(exc, "code", None)
    hint = getattr(exc, "hint", None)
    details = getattr(exc, "details", None)
    text = f"{msg}" + (f"  [{code}]" if code else "")
    if details and details != msg:
        text += f"\n  {details}"
    if hint:
        text += f"\n  hint: {hint}"
    if code in ("PGRST301", "PGRST303") or "JWT" in str(msg):
        text += "\n  → session expired? Run: sbcli auth login EMAIL"
    if code == "42501" and "permission denied" in str(msg):
        text += "\n  → permission denied: are you logged in? (`sbcli auth whoami`)"
    if code in ("PGRST202", "PGRST205", "42P01", "42883"):
        text += "\n  → schema missing or outdated? Run: sbcli db status / sbcli db reset"
    return text


def _hoist_target(argv: list[str]) -> list[str]:
    """Allow `-t cloud` / `--target cloud` anywhere, e.g. `sbcli products list -t cloud`."""
    args, hoisted, i = argv[1:], [], 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--target") and i + 1 < len(args):
            hoisted += [a, args[i + 1]]
            del args[i : i + 2]
        elif a.startswith("--target="):
            hoisted.append(a)
            del args[i]
        elif a == "--":
            break
        else:
            i += 1
    return [argv[0], *hoisted, *args]


def main() -> None:
    sys.argv = _hoist_target(sys.argv)
    try:
        app()
    except CliError as exc:
        err_console.print(f"[red]✗[/red] {exc}")
        sys.exit(1)
    except APIError as exc:
        err_console.print(f"[red]✗ database:[/red] {_explain_api_error(exc)}")
        sys.exit(1)
    except AuthError as exc:
        err_console.print(f"[red]✗ auth:[/red] {exc}")
        sys.exit(1)
    except StorageApiError as exc:
        err_console.print(f"[red]✗ storage:[/red] {exc}")
        sys.exit(1)
    except httpx.HTTPError as exc:
        err_console.print(f"[red]✗ network:[/red] {exc}\n  → is the target reachable? (`sbcli target`, `sbcli local status`)")
        sys.exit(1)
    except psycopg.Error as exc:
        err_console.print(f"[red]✗ postgres:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
