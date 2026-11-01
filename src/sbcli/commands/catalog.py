# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""categories + products — read via views, act via RPC (data lives in schema mobappdata)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

import typer

from sbcli import context
from sbcli.errors import CliError
from sbcli.output import console, info, ok, print_json, table

categories_app = typer.Typer(help="Shop categories: list, add, rename, remove.", no_args_is_help=True)
products_app = typer.Typer(help="Shop products: search, show, add/update/remove, VAT, import/export.",
                           no_args_is_help=True)

IMPORT_BATCH = 500
CAT_HELP = "Category id or name (case-insensitive); 'none' = without category."


# =============================================================================
# categories
# =============================================================================
@categories_app.command("list")
def categories_list(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")):
    """All categories with product count and price range (view category_stats)."""
    rows = context.get(ctx).user().table("category_stats").select("*").order("id").execute().data
    if as_json:
        print_json(rows)
        return
    table(rows, ["id", "name", "products", ("min_price", "min"), ("avg_price", "avg"), ("max_price", "max")],
          title=f"{len(rows)} categories", right=["id", "products", "min_price", "avg_price", "max_price"])


@categories_app.command("add")
def categories_add(
    ctx: typer.Context,
    name: str,
    cid: Optional[int] = typer.Option(None, "--id", help="Explicit id (default: max+1)."),
):
    """Create a category (returns the existing one if the name is taken)."""
    new_id = context.get(ctx).user().rpc("add_category", {"p_name": name, "p_id": cid}).execute().data
    ok(f"Category #{new_id} '{name}'.")


@categories_app.command("rename")
def categories_rename(ctx: typer.Context, category: str = typer.Argument(..., help="Id or name."), new_name: str = typer.Argument(...)):
    """Rename a category."""
    cid = context.get(ctx).user().rpc("rename_category", {"p_category": category, "p_name": new_name}).execute().data
    ok(f"Category #{cid} is now '{new_name}'.")


@categories_app.command("remove")
def categories_remove(
    ctx: typer.Context,
    category: str = typer.Argument(..., help="Id or name."),
    move_to: Optional[str] = typer.Option(None, "--move-to", "-m", help="Move its products there (else: no category)."),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a category; its products move to --move-to or become uncategorized."""
    if not yes:
        target = f"to '{move_to}'" if move_to else "to 'no category'"
        typer.confirm(f"Delete category '{category}' and move its products {target}?", abort=True)
    moved = context.get(ctx).user().rpc("delete_category", {"p_category": category, "p_move_to": move_to}).execute().data
    ok(f"Category '{category}' deleted, {moved} product(s) moved.")


# =============================================================================
# products
# =============================================================================
PRODUCT_COLS = ["id", "name", "category", "unit", "price", ("vat_percent", "vat %"), ("price_net", "net"),
                ("picture_count", "pics")]
PRODUCT_RIGHT = ["id", "price", "vat_percent", "price_net", "picture_count"]


def _search(ctx, **params) -> list[dict]:
    return context.get(ctx).user().rpc("search_products", {f"p_{k}": v for k, v in params.items()}).execute().data


@products_app.command("list")
def products_list(
    ctx: typer.Context,
    search: Optional[str] = typer.Option(None, "--search", "-s", help="Name: substring or fuzzy."),
    category: Optional[str] = typer.Option(None, "--category", "-c", help=CAT_HELP),
    min_price: Optional[float] = typer.Option(None, "--min"),
    max_price: Optional[float] = typer.Option(None, "--max"),
    vat: Optional[float] = typer.Option(None, "--vat", help="e.g. 0.07 or 0.19"),
    unit: Optional[str] = typer.Option(None, "--unit", "-u", help="e.g. PC, PK, KG"),
    limit: int = typer.Option(100, "--limit", "-l"),
    offset: int = typer.Option(0, "--offset"),
    as_json: bool = typer.Option(False, "--json"),
):
    """List / filter products (RPC search_products → rows of product_details)."""
    rows = _search(ctx, query=search, category=category, min_price=min_price, max_price=max_price,
                   vat=vat, unit=unit, limit=limit, offset=offset)
    if as_json:
        print_json(rows)
    else:
        table(rows, PRODUCT_COLS, title=f"{len(rows)} products", right=PRODUCT_RIGHT)


@products_app.command("search")
def products_search(
    ctx: typer.Context,
    query: str,
    category: Optional[str] = typer.Option(None, "--category", "-c", help=CAT_HELP),
    as_json: bool = typer.Option(False, "--json"),
):
    """Shortcut: products by name, best match first (typos are OK: 'piza')."""
    rows = _search(ctx, query=query, category=category)
    if as_json:
        print_json(rows)
    else:
        table(rows, PRODUCT_COLS, title=f"{len(rows)} products matching '{query}'", right=PRODUCT_RIGHT)


@products_app.command("show")
def products_show(ctx: typer.Context, pid: int = typer.Argument(..., metavar="ID"), as_json: bool = typer.Option(False, "--json")):
    """One product with VAT breakdown and pictures."""
    client = context.get(ctx).user()
    rows = client.table("product_details").select("*").eq("id", pid).execute().data
    if not rows:
        raise CliError(f"No product with id {pid}.")
    p = rows[0]
    pics = (client.table("pictures").select("id,file_name,title,is_primary,size_bytes,created_at")
            .eq("product_id", pid).order("id").execute().data)
    if as_json:
        print_json({"product": p, "pictures": pics})
        return
    cat = "no category" if p["category_id"] is None else f"{p['category']} (#{p['category_id']})"
    console.print(f"[bold]#{p['id']} {p['name']}[/bold]   [dim]{cat}[/dim]")
    console.print(f"price  {p['price']:,.2f} per {p['unit']}   = net {p['price_net']:,.2f} + "
                  f"VAT {p['vat_percent']:g} % {p['vat_amount']:,.2f}")
    table(pics, ["id", "file_name", "title", ("is_primary", "primary"), ("size_bytes", "bytes"), "created_at"],
          title="pictures", right=["id", "size_bytes"])


@products_app.command("add")
def products_add(
    ctx: typer.Context,
    name: str,
    unit: str = typer.Option(..., "--unit", "-u", help="PC, PK, KG, …"),
    price: float = typer.Option(..., "--price", "-p", help="Shelf price incl. VAT."),
    vat: float = typer.Option(0.19, "--vat", help="Fraction: 0.07 or 0.19."),
    category: Optional[str] = typer.Option(None, "--category", "-c", help=CAT_HELP),
    pid: Optional[int] = typer.Option(None, "--id", help="Explicit id (default: max+1)."),
):
    """Create a product (RPC add_product)."""
    new_id = context.get(ctx).user().rpc("add_product", {"p_id": pid, "p_name": name, "p_unit": unit, "p_price": price,
                                                         "p_vat": vat, "p_category": category}).execute().data
    ok(f"Product #{new_id} '{name}' created.")


@products_app.command("update")
def products_update(
    ctx: typer.Context,
    pid: int = typer.Argument(..., metavar="ID"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    unit: Optional[str] = typer.Option(None, "--unit", "-u"),
    price: Optional[float] = typer.Option(None, "--price", "-p"),
    vat: Optional[float] = typer.Option(None, "--vat"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help=CAT_HELP),
):
    """Change only the given fields (RPC update_product)."""
    context.get(ctx).user().rpc("update_product", {"p_id": pid, "p_name": name, "p_unit": unit, "p_price": price,
                                                   "p_vat": vat, "p_category": category}).execute()
    ok(f"Product #{pid} updated.")


@products_app.command("remove")
def products_remove(
    ctx: typer.Context,
    pid: int = typer.Argument(..., metavar="ID"),
    with_pictures: bool = typer.Option(False, "--with-pictures", help="Also delete its pictures from storage."),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a product (pictures are detached, or deleted with --with-pictures)."""
    from sbcli.commands.images import delete_pictures  # local import: avoid cycle

    client = context.get(ctx).user()
    rows = client.table("products").select("id,name").eq("id", pid).execute().data
    if not rows:
        raise CliError(f"No product with id {pid}.")
    if not yes:
        typer.confirm(f"Delete product #{pid} {rows[0]['name']}?", abort=True)
    if with_pictures:
        ids = [r["id"] for r in client.table("pictures").select("id").eq("product_id", pid).execute().data]
        delete_pictures(client, ids)
    client.rpc("delete_product", {"p_id": pid}).execute()
    ok(f"Product #{pid} deleted.")


@products_app.command("vat")
def products_vat(ctx: typer.Context):
    """Products per VAT rate (view vat_rates)."""
    rows = context.get(ctx).user().table("vat_rates").select("*").order("vat").execute().data
    table(rows, [("vat_percent", "vat %"), "products", ("avg_price", "avg price"), "categories"],
          title="VAT rates", right=["vat_percent", "products", "avg_price"])


# --- import / export ---------------------------------------------------------
def _csv_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
                for row in csv.DictReader(fh, dialect=dialect)]


def _num(v: str | None, kind=float):
    if v is None or str(v).strip() == "":
        return None
    return kind(str(v).replace(",", "."))


def _products_from_csv(path: Path) -> list[dict]:
    """Columns: id (optional), name, category (name or id) or category_id, unit, price, vat"""
    out = []
    for row in _csv_rows(path):
        item = {"name": row.get("name") or None, "unit": row.get("unit") or None}
        if row.get("id"):
            item["id"] = _num(row["id"], int)
        for key in ("price", "vat"):
            if row.get(key):
                item[key] = _num(row[key])
        cat = row.get("category") or row.get("category_id")
        if cat:
            item["category"] = cat
        out.append(item)
    return out


def _categories_from_csv(path: Path) -> list[dict]:
    """Columns: id (optional), name"""
    return [{"name": r["name"], **({"id": int(r["id"])} if r.get("id") else {})} for r in _csv_rows(path)]


@products_app.command("import")
def products_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, dir_okay=False, help=".json ({categories,products}) or products .csv"),
    categories_csv: Optional[Path] = typer.Option(None, "--categories", exists=True, dir_okay=False,
                                                  help="Categories .csv (id,name) to import first."),
):
    """Bulk upsert by id (RPC import_catalog). Unknown category names are created."""
    client = context.get(ctx).user()
    if file.suffix.lower() == ".json":
        data = json.loads(file.read_text(encoding="utf-8"))
        categories, products = data.get("categories", []), data.get("products", [])
    elif file.suffix.lower() in (".csv", ".tsv", ".txt"):
        categories, products = [], _products_from_csv(file)
    else:
        raise CliError("Unsupported file type — use .json or .csv")
    if categories_csv:
        categories = _categories_from_csv(categories_csv) + categories

    if categories:
        res = client.rpc("import_catalog", {"p_data": {"categories": categories}}).execute().data
        ok(f"{res['categories']} categories imported")
    done = 0
    for i in range(0, len(products), IMPORT_BATCH):
        chunk = products[i : i + IMPORT_BATCH]
        client.rpc("import_catalog", {"p_data": {"products": chunk}}).execute()
        done += len(chunk)
        info(f"  … {done}/{len(products)} products")
    ok(f"{done} products imported")


def _sql_str(s: str | None) -> str:
    return "null" if s is None else "'" + str(s).replace("'", "''") + "'"


def _export_sql(cats: list[dict], prods: list[dict]) -> str:
    """Same layout as sql/04_catalog_data.sql, so the output can replace that file."""
    cat_rows = ",\n        ".join(f"({c['id']}, {_sql_str(c['name'])})" for c in cats)
    prod_rows = ",\n        ".join(
        f"({p['id']}, {_sql_str(p['name'])}, {'null' if p['category_id'] is None else p['category_id']}, "
        f"{_sql_str(p['unit'])}, {p['price']:.2f}, {p['vat']:.2f})" for p in prods)
    out = "-- exported by `sbcli products export` (layout of sql/04_catalog_data.sql)\n\n"
    if cats:
        out += f"insert into mobappdata.categories (id, name)\nvalues  {cat_rows};\n\n"
    if prods:
        out += f"insert into mobappdata.products (id, name, category_id, unit, price, vat)\nvalues  {prod_rows};\n\n"
    return out + "-- explicit ids were used → move the identity sequences past them\nselect mobappdata.sync_id_sequences();\n"


@products_app.command("export")
def products_export(ctx: typer.Context, file: Path = typer.Argument(Path("catalog.json"), help=".json, .csv or .sql")):
    """Write categories + products as JSON (re-importable), CSV (products) or SQL inserts."""
    client = context.get(ctx).user()
    cats = client.table("categories").select("id,name").order("id").execute().data
    prods, offset = [], 0
    while True:  # PostgREST caps responses (Supabase default: 1000 rows) → page through
        page = (client.table("product_details").select("id,name,category_id,category,unit,price,vat")
                .order("id").range(offset, offset + 999).execute().data)
        prods += page
        if len(page) < 1000:
            break
        offset += 1000
    suffix = file.suffix.lower()
    if suffix == ".json":
        file.write_text(json.dumps({"categories": cats, "products": [
            {k: p[k] for k in ("id", "name", "category_id", "unit", "price", "vat")} for p in prods]},
            indent=2, ensure_ascii=False), encoding="utf-8")
    elif suffix == ".csv":
        with file.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "name", "category_id", "category", "unit", "price", "vat"])
            w.writeheader()
            w.writerows(prods)
    elif suffix == ".sql":
        file.write_text(_export_sql(cats, prods), encoding="utf-8")
    else:
        raise CliError("Unsupported file type — use .json, .csv or .sql")
    ok(f"Exported {len(cats)} categories and {len(prods)} products to {file}")
