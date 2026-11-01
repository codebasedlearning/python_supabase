# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""users: admin operations on auth.users (needs the service-role / secret key)."""

from __future__ import annotations

import secrets
import string
import uuid
from typing import Optional

import typer

from sbcli import context
from sbcli.errors import CliError
from sbcli.output import console, ok, print_json, table

users_app = typer.Typer(help="Add / remove / list users (admin, uses the service-role key).", no_args_is_help=True)


def _gen_password(n: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "-_.!"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _all_users(admin) -> list:
    users, page = [], 1
    while True:
        batch = admin.auth.admin.list_users(page=page, per_page=200)
        users.extend(batch)
        if len(batch) < 200:
            return users
        page += 1


def resolve_user_id(admin, ident: str) -> tuple[str, str | None]:
    """Accept a UUID or an e-mail address."""
    try:
        return str(uuid.UUID(ident)), None
    except ValueError:
        pass
    row = admin.table("profiles").select("id,email").ilike("email", ident).limit(1).execute().data
    if row:
        return row[0]["id"], row[0]["email"]
    for u in _all_users(admin):  # profile missing? fall back to auth
        if (u.email or "").lower() == ident.lower():
            return u.id, u.email
    raise CliError(f"No user with e-mail or id '{ident}'.")


@users_app.command("list")
def list_users(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")):
    """All auth users merged with their profile."""
    admin = context.get(ctx).admin()
    profiles = {p["id"]: p for p in admin.table("profiles").select("*").execute().data}
    rows = []
    for u in _all_users(admin):
        p = profiles.get(u.id, {})
        rows.append(
            {
                "id": u.id,
                "email": u.email,
                "name": p.get("display_name"),
                "confirmed": bool(u.email_confirmed_at),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_sign_in_at": u.last_sign_in_at.isoformat() if u.last_sign_in_at else None,
                "messages": p.get("messages"),
                "pictures": p.get("pictures"),
                "profile": u.id in profiles,
            }
        )
    rows.sort(key=lambda r: r["email"] or "")
    if as_json:
        print_json(rows)
    else:
        table(rows, ["email", "name", "confirmed", "created_at", "last_sign_in_at", "messages", "pictures"],
              title=f"{len(rows)} users", right=["messages", "pictures"])


@users_app.command("add")
def add_user(
    ctx: typer.Context,
    email: str,
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Omit to auto-generate one."),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Display name."),
    require_confirmation: bool = typer.Option(
        False, "--require-confirmation", "--unconfirmed",
        help="Create the user with an UNconfirmed e-mail: they can't log in until confirmed "
             "(no mail is sent — use `users confirm`). Default: confirmed, can log in right away."),
):
    """Create a user who can log in right away. The trigger on auth.users creates the profile + audit entry."""
    admin = context.get(ctx).admin()
    generated = password is None
    password = password or _gen_password()
    resp = admin.auth.admin.create_user(
        {
            "email": email,
            "password": password,
            "email_confirm": not require_confirmation,
            "user_metadata": {"display_name": name} if name else {},
        }
    )
    ok(f"User created: {resp.user.email}  ({resp.user.id})")
    if generated:
        console.print(f"  password: [bold]{password}[/bold]   (shown once)")
    if require_confirmation:
        console.print("  [yellow]e-mail NOT confirmed → login is refused until you run:[/yellow] "
                      f"sbcli users confirm {resp.user.email}")


@users_app.command("confirm")
def confirm_user(ctx: typer.Context, ident: str = typer.Argument(..., help="E-mail or user id.")):
    """Mark a user's e-mail as confirmed, so they can log in."""
    admin = context.get(ctx).admin()
    uid, email = resolve_user_id(admin, ident)
    admin.auth.admin.update_user_by_id(uid, {"email_confirm": True})
    ok(f"{email or uid} is confirmed and can log in now.")


@users_app.command("remove")
def remove_user(
    ctx: typer.Context,
    ident: str = typer.Argument(..., help="E-mail or user id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="No confirmation prompt."),
):
    """Delete a user. Profile + memberships cascade, messages stay as '(deleted user)'."""
    admin = context.get(ctx).admin()
    uid, email = resolve_user_id(admin, ident)
    if not yes:
        typer.confirm(f"Really delete user {email or uid}?", abort=True)
    admin.auth.admin.delete_user(uid)
    ok(f"Deleted user {email or uid}.")


@users_app.command("set-password")
def set_password(
    ctx: typer.Context,
    ident: str = typer.Argument(..., help="E-mail or user id."),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
):
    """Reset a user's password."""
    admin = context.get(ctx).admin()
    uid, email = resolve_user_id(admin, ident)
    admin.auth.admin.update_user_by_id(uid, {"password": password})
    ok(f"Password changed for {email or uid}.")


@users_app.command("audit")
def audit(
    ctx: typer.Context,
    ident: Optional[str] = typer.Argument(None, help="Filter by e-mail or id."),
    limit: int = typer.Option(30, "--limit", "-l"),
):
    """Lifecycle log written by the auth.users triggers (created, signed_in, …)."""
    admin = context.get(ctx).admin()
    q = admin.table("user_audit").select("at,email,action,details,user_id").order("at", desc=True).limit(limit)
    if ident:
        try:
            q = q.eq("user_id", str(uuid.UUID(ident)))
        except ValueError:
            q = q.ilike("email", ident)
    table(q.execute().data, ["at", "email", "action", "details"], title="user audit")
