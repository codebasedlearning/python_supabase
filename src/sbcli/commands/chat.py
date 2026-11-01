# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""chat: one message board for all signed-in users — send, read, listen (live)."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List

import typer

from sbcli import context, session
from sbcli.errors import CliError
from sbcli.output import console, info, ok

chat_app = typer.Typer(help="Messages: post, read, edit/delete your own, get notified live.", no_args_is_help=True)


def _ts(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return "--:--"
    return dt.strftime("%H:%M") if dt.date() == datetime.now().date() else dt.strftime("%d.%m. %H:%M")


def _print_msg(author: str, body: str, created_at: str, mine: bool = False, bell: bool = False,
               mid: int | None = None, edited: bool = False, prefix: str = "") -> None:
    colour = "green" if mine else "magenta"
    num = f"[dim]#{mid}[/dim] " if mid is not None else ""
    note = " [dim](edited)[/dim]" if edited else ""
    console.print(f"{prefix}{num}[dim]{_ts(created_at)}[/dim] [{colour}]{author}[/{colour}]: {body}{note}",
                  highlight=False)
    if bell and not mine:
        sys.stdout.write("\a")
        sys.stdout.flush()


def _latest(client, limit: int, after_id: int = 0) -> list[dict]:
    q = client.table("messages").select("*").gt("id", after_id).order("id", desc=True).limit(limit)
    return list(reversed(q.execute().data))


def _show(m: dict, bell: bool = False, prefix: str = "") -> None:
    _print_msg(m["author"], m["body"], m["created_at"], mine=m["mine"], bell=bell,
               mid=m["id"], edited=m.get("edited_at") is not None, prefix=prefix)


@chat_app.command("send")
def send(ctx: typer.Context, message: List[str] = typer.Argument(..., help="Text, or '-' for stdin.")):
    """Post a message (RPC post_message)."""
    body = (sys.stdin.read() if message == ["-"] else " ".join(message)).strip()
    if not body:
        raise CliError("Empty message.")
    mid = context.get(ctx).user().rpc("post_message", {"p_body": body}).execute().data
    ok(f"sent (#{mid})")


@chat_app.command("edit")
def edit(ctx: typer.Context, mid: int = typer.Argument(..., metavar="ID"),
         message: List[str] = typer.Argument(..., help="New text, or '-' for stdin.")):
    """Change one of YOUR messages (RPC edit_message)."""
    body = (sys.stdin.read() if message == ["-"] else " ".join(message)).strip()
    if not body:
        raise CliError("Empty message.")
    context.get(ctx).user().rpc("edit_message", {"p_id": mid, "p_body": body}).execute()
    ok(f"#{mid} edited")


@chat_app.command("delete")
def delete(ctx: typer.Context, mid: int = typer.Argument(..., metavar="ID"),
           yes: bool = typer.Option(False, "--yes", "-y")):
    """Delete one of YOUR messages (RPC delete_message)."""
    if not yes:
        typer.confirm(f"Delete message #{mid}?", abort=True)
    context.get(ctx).user().rpc("delete_message", {"p_id": mid}).execute()
    ok(f"#{mid} deleted")


@chat_app.command("read")
def read(ctx: typer.Context, limit: int = typer.Option(20, "--limit", "-l", help="How many of the latest.")):
    """Show the latest messages, oldest first, with ids (view messages)."""
    rows = _latest(context.get(ctx).user(), limit)
    if not rows:
        info("no messages yet — be the first: sbcli chat send hello")
    for m in rows:
        _show(m)


@chat_app.command("listen")
def listen(
    ctx: typer.Context,
    history: int = typer.Option(5, "--history", "-n", help="Show the last N messages first."),
    poll: float = typer.Option(0, "--poll", help="Poll every N seconds instead of Realtime (0 = Realtime)."),
    bell: bool = typer.Option(False, "--bell", help="Ring the terminal bell on new messages."),
):
    """Get notified of new, edited and deleted messages as they happen. Ctrl+C to stop."""
    c = context.get(ctx)
    client = c.user()
    me = c.user_id()
    last_id = 0
    for m in _latest(client, history) if history > 0 else []:
        _show(m)
        last_id = m["id"]
    if not last_id:  # nothing shown: start after the newest message
        newest = client.table("messages").select("id").order("id", desc=True).limit(1).execute().data
        last_id = newest[0]["id"] if newest else 0
    try:
        if poll > 0:
            info(f"polling every {poll:g}s — Ctrl+C to stop")
            _poll_loop(c, client, last_id, poll, me, bell)
        else:
            info("listening via Realtime — Ctrl+C to stop")
            asyncio.run(_realtime_loop(c, me, bell))
    except KeyboardInterrupt:
        info("bye 👋")


def _poll_loop(c, client, last_id: int, every: float, me: str, bell: bool) -> None:
    """New messages by id, edits by edited_at. (Deletions can't be seen by polling — the row is gone.)"""
    since = datetime.now(timezone.utc)
    while True:
        time.sleep(every)
        c.refresh_if_needed(client)
        for m in _latest(client, 200, after_id=last_id):
            _show(m, bell=bell)
            last_id = m["id"]
        edited = (client.table("messages").select("*").gt("edited_at", since.isoformat()).lte("id", last_id)
                  .order("edited_at").execute().data)
        for m in edited:
            _show(m, bell=bell, prefix="[yellow]✎[/yellow] ")
            since = max(since, datetime.fromisoformat(m["edited_at"].replace("Z", "+00:00")))


async def _realtime_loop(c, me: str, bell: bool) -> None:
    from supabase import AsyncClientOptions, acreate_client

    # realtime-py reports connection trouble only via logging → show it
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("realtime: %(message)s"))
    rt_log = logging.getLogger("realtime")
    rt_log.addHandler(handler)
    rt_log.setLevel(logging.ERROR)

    s = c.settings
    data = session.load(c.target)
    client = await acreate_client(s.url, s.anon_key,
                                  options=AsyncClientOptions(auto_refresh_token=True, persist_session=False))
    resp = await client.auth.set_session(data["access_token"], data["refresh_token"])
    session.save(c.target, resp.session)
    await client.realtime.set_auth(resp.session.access_token)

    authors: dict[str, str] = {}
    queue: asyncio.Queue = asyncio.Queue()

    def on_change(payload):
        data = payload.get("data") or payload
        kind = str(data.get("type") or data.get("eventType") or "").split(".")[-1].upper()
        queue.put_nowait((kind, data.get("record") or {}, data.get("old_record") or {}))

    def on_state(state, err):
        if err:
            console.print(f"[red]realtime: {state} {err}[/red]")
        elif str(state).endswith("SUBSCRIBED"):
            info("realtime: subscribed ✓")

    # the table lives in mobappdata (not exposed to the API) — Realtime reads the WAL, that's fine
    channel = client.channel("sbcli-messages")
    channel.on_postgres_changes("*", schema="mobappdata", table="messages", callback=on_change)
    try:
        await asyncio.wait_for(channel.subscribe(on_state), timeout=25)
    except Exception as exc:  # incl. TimeoutError
        raise CliError(f"Realtime not reachable at {s.url}/realtime/v1 ({exc or 'timeout'}).\n"
                       "  → is the realtime service running? Fallback: sbcli chat listen --poll 2") from exc

    try:
        while True:
            kind, rec, old = await queue.get()
            if kind == "DELETE":
                console.print(f"[red]✗[/red] [dim]#{old.get('id', '?')} was deleted[/dim]")
                continue
            aid = rec.get("author_id")
            if aid and aid not in authors:
                rows = (await client.table("profiles").select("display_name").eq("id", aid).execute()).data
                authors[aid] = rows[0]["display_name"] if rows else "(unknown)"
            _print_msg(authors.get(aid, "(deleted user)"), rec.get("body", ""), rec.get("created_at", ""),
                       mine=aid == me, bell=bell, mid=rec.get("id"), edited=kind == "UPDATE",
                       prefix="[yellow]✎[/yellow] " if kind == "UPDATE" else "")
    finally:
        current = await client.auth.get_session()
        if current:
            session.save(c.target, current)
        await client.remove_all_channels()
