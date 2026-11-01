# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""query: generic, filterable read access to the views (RLS applies)."""

from __future__ import annotations

from typing import List, Optional

import typer

from sbcli import context
from sbcli.errors import CliError
from sbcli.output import console, print_json, table

VIEWS = {
    "products": "products, plain (id, name, category_id, unit, price, vat)",
    "categories": "categories, plain (id, name)",
    "product_details": "products + category name, VAT breakdown, pictures",
    "category_stats": "categories + product count, price range",
    "vat_rates": "products per VAT rate",
    "pictures": "pictures with product + uploader",
    "messages": "messages with author name",
    "profiles": "users + activity counters",
    "user_audit": "auth.users lifecycle log",
}

_OPS = {"=": "eq", "!=": "neq", ">=": "gte", "<=": "lte", ">": "gt", "<": "lt", "~": "ilike", "@": "contains"}


def _apply_where(q, expr: str):
    for sym in ("!=", ">=", "<=", "=", ">", "<", "~", "@"):  # longest first
        if sym in expr:
            col, val = expr.split(sym, 1)
            op = _OPS[sym]
            if op == "ilike" and "%" not in val and "*" not in val:
                val = f"%{val}%"
            if op == "contains":  # array column contains value(s): tags@a,b
                return q.contains(col.strip(), [v.strip() for v in val.split(",")])
            if val.lower() == "null" and op in ("eq", "neq"):
                return q.is_(col.strip(), "null") if op == "eq" else q.not_.is_(col.strip(), "null")
            return getattr(q, op)(col.strip(), val.strip())
    raise CliError(f"Can't parse --where '{expr}'. Use col=val, col!=val, col>val, col<val, col~text, col@val "
                   "(and quote it, or your shell treats > as a redirect)")


def query(
    ctx: typer.Context,
    view: Optional[str] = typer.Argument(None, help="View name (omit to list them)."),
    where: List[str] = typer.Option([], "--where", "-w", help="Repeatable, quote it! 'col=val' 'col>val' 'col~text' 'arraycol@val'"),
    columns: Optional[str] = typer.Option(None, "--columns", "-c", help="Comma separated."),
    order: Optional[str] = typer.Option(None, "--order", "-o", help="col or col.desc"),
    limit: int = typer.Option(50, "--limit", "-l"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Query any view, e.g.: sbcli query product_details -w 'price>2' -w 'category~drinks' -o price.desc"""
    if not view:
        table([{"view": k, "what": v} for k, v in VIEWS.items()], ["view", "what"], title="queryable views")
        return
    if view not in VIEWS:
        console.print(f"[yellow]'{view}' is not one of the documented views — trying anyway.[/yellow]")
    q = context.get(ctx).user().table(view).select(columns or "*")
    for w in where:
        q = _apply_where(q, w)
    if order:
        col, _, direction = order.partition(".")
        q = q.order(col, desc=direction == "desc")
    rows = q.limit(limit).execute().data
    if as_json:
        print_json(rows)
    else:
        table(rows, title=f"{view} ({len(rows)} rows)")
