# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""auth: signup / login / logout / whoami  •  profile: show / set"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import typer

from sbcli import context, session
from sbcli.errors import CliError
from sbcli.output import console, info, ok, table

auth_app = typer.Typer(help="Sign up, log in, log out, who am I.", no_args_is_help=True)
profile_app = typer.Typer(help="Your own profile (fed by triggers on auth.users).", no_args_is_help=True)


@auth_app.command()
def signup(
    ctx: typer.Context,
    email: str,
    password: str = typer.Option(..., "--password", "-p", prompt=True, hide_input=True, confirmation_prompt=True),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Display name for the profile."),
):
    """Self-register a new account (anon key)."""
    c = context.get(ctx)
    resp = c.anon().auth.sign_up(
        {"email": email, "password": password, "options": {"data": {"display_name": name} if name else {}}}
    )
    if resp.session:
        session.save(c.target, resp.session)
        ok(f"Signed up and logged in as {email} on '{c.target}'.")
    else:
        ok(f"Signed up {email}. Confirm the e-mail, then run: sbcli auth login {email}")
        if c.target == "local":
            info("Local stack: confirmation mails land in Mailpit → http://127.0.0.1:54324")


@auth_app.command()
def login(
    ctx: typer.Context,
    email: str,
    password: str = typer.Option(..., "--password", "-p", prompt=True, hide_input=True),
):
    """Log in with e-mail + password; the session is stored per target."""
    c = context.get(ctx)
    resp = c.anon().auth.sign_in_with_password({"email": email, "password": password})
    if not resp.session:
        raise CliError("Login failed (no session returned).")
    session.save(c.target, resp.session)
    ok(f"Logged in as {resp.user.email} on '{c.target}'.")


@auth_app.command()
def logout(ctx: typer.Context, everywhere: bool = typer.Option(False, help="Revoke all sessions of this user.")):
    """Log out (revokes the refresh token server-side, deletes the local session)."""
    c = context.get(ctx)
    if not session.load(c.target):
        info(f"Not logged in on '{c.target}'.")
        return
    try:
        c.user().auth.sign_out({"scope": "global" if everywhere else "local"})
    except Exception as exc:  # token already dead? fine, still forget it locally
        info(f"server sign-out skipped: {exc}")
    session.clear(c.target)
    ok(f"Logged out from '{c.target}'.")


@auth_app.command()
def whoami(ctx: typer.Context):
    """Show the signed-in user and session expiry."""
    c = context.get(ctx)
    client = c.user()
    user = client.auth.get_user().user
    data = session.load(c.target) or {}
    profile = client.table("profiles").select("*").eq("id", user.id).maybe_single().execute()
    exp = datetime.fromtimestamp(data["expires_at"]).strftime("%H:%M:%S") if data.get("expires_at") else "?"
    console.print(f"[bold]{user.email}[/bold]  ({user.id})")
    console.print(f"target       {c.target}  →  {c.settings.url}")
    if profile and profile.data:
        console.print(f"display name {profile.data['display_name']}")
    console.print(f"token until  {exp}  (refreshed automatically)")


# ---------------------------------------------------------------------------
@profile_app.command("show")
def profile_show(ctx: typer.Context):
    """Show your profile and your latest audit events."""
    c = context.get(ctx)
    client = c.user()
    uid = c.user_id()
    prof = client.table("profiles").select("*").eq("id", uid).single().execute().data
    table([prof], ["email", "display_name", "bio", "created_at", "last_sign_in_at", "pictures", "messages"])
    events = client.table("user_audit").select("action,details,at").eq("user_id", uid).order("at", desc=True).limit(10).execute().data
    table(events, ["at", "action", "details"], title="recent events")


@profile_app.command("set")
def profile_set(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    bio: Optional[str] = typer.Option(None, "--bio"),
):
    """Change display name and/or bio (RPC update_my_profile)."""
    if name is None and bio is None:
        raise CliError("Nothing to change — use --name and/or --bio.")
    c = context.get(ctx)
    display = c.user().rpc("update_my_profile", {"p_display_name": name, "p_bio": bio}).execute().data
    ok(f"Profile updated: {display}")
