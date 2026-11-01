# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""Rendering helpers: tables, JSON, messages."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "✓" if value else "·"
    if isinstance(value, (list, tuple)):
        return " | ".join(fmt(v) for v in value)
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-" and value[10] in "T ":
        # ISO timestamp from PostgREST → "YYYY-MM-DD HH:MM"
        return value[:16].replace("T", " ")
    return str(value)


def table(
    rows: Iterable[dict],
    columns: Sequence[str | tuple[str, str]] | None = None,
    title: str | None = None,
    right: Sequence[str] = (),
) -> None:
    """columns: keys, or (key, header) pairs. Missing columns → all keys of the first row."""
    rows = list(rows)
    if not rows:
        console.print(f"[dim]{title + ': ' if title else ''}nothing found[/dim]")
        return
    cols = [(c, c) if isinstance(c, str) else c for c in (columns or rows[0].keys())]
    t = Table(title=title, header_style="bold cyan", title_justify="left", show_lines=False)
    for key, header in cols:
        t.add_column(header, justify="right" if key in right else "left", overflow="fold")
    for row in rows:
        t.add_row(*(fmt(row.get(key)) for key, _ in cols))
    console.print(t)


def _default(o: Any):
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=_default, ensure_ascii=False))


def ok(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def warn(msg: str) -> None:
    err_console.print(f"[yellow]![/yellow] {msg}")


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")
