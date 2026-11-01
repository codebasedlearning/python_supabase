# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""images: binaries in storage bucket `pictures`, metadata in table public.pictures."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from typing import List, Optional

import typer

from sbcli import context
from sbcli.config import BUCKET
from sbcli.errors import CliError
from sbcli.output import console, info, ok, print_json, table, warn

images_app = typer.Typer(help="Upload / download / list pictures (Supabase Storage, S3-backed).", no_args_is_help=True)

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")


def _folder(product_id: int | None) -> str:
    return f"product-{product_id}" if product_id is not None else "unassigned"


def _picture(client, pid: int) -> dict:
    rows = client.table("pictures").select("*").eq("id", pid).execute().data
    if not rows:
        raise CliError(f"No picture with id {pid}.")
    return rows[0]


def delete_pictures(client, ids: list[int]) -> None:
    """Row first (RPC returns the path), then the object."""
    paths = []
    for pid in ids:
        paths.append(client.rpc("remove_picture", {"p_picture_id": pid}).execute().data)
    if paths:
        client.storage.from_(BUCKET).remove(paths)
        ok(f"Deleted {len(paths)} picture(s).")


@images_app.command("upload")
def upload(
    ctx: typer.Context,
    files: List[Path] = typer.Argument(..., exists=True, dir_okay=False, help="One or more image files."),
    product: Optional[int] = typer.Option(None, "--product", "-p", help="Attach to product id."),
    title: Optional[str] = typer.Option(None, "--title"),
    primary: Optional[bool] = typer.Option(None, "--primary/--not-primary",
                                           help="Default: first picture of a product becomes primary."),
):
    """Upload into the bucket and register it (RPC add_picture)."""
    client = context.get(ctx).user()
    bucket = client.storage.from_(BUCKET)
    for f in files:
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if not mime.startswith("image/"):
            raise CliError(f"{f.name}: not an image ({mime})")
        path = f"{_folder(product)}/{uuid.uuid4().hex}{f.suffix.lower()}"
        data = f.read_bytes()
        bucket.upload(path, data, {"content-type": mime, "upsert": "false"})
        try:
            pid = client.rpc(
                "add_picture",
                {"p_object_path": path, "p_file_name": f.name, "p_mime_type": mime, "p_size_bytes": len(data),
                 "p_title": title, "p_product_id": product, "p_is_primary": primary},
            ).execute().data
        except Exception:
            bucket.remove([path])  # don't leave an orphan behind
            raise
        ok(f"{f.name} → picture #{pid}  ({path}, {len(data):,} bytes)")


@images_app.command("list")
def list_pictures(
    ctx: typer.Context,
    product: Optional[int] = typer.Option(None, "--product", "-p", help="Only pictures of this product id."),
    unassigned: bool = typer.Option(False, "--unassigned", help="Only pictures without product."),
    as_json: bool = typer.Option(False, "--json"),
):
    """Pictures with product and uploader (view pictures)."""
    q = context.get(ctx).user().table("pictures").select("*").order("id")
    if product:
        q = q.eq("product_id", product)
    if unassigned:
        q = q.is_("product_id", "null")
    rows = q.execute().data
    if as_json:
        print_json(rows)
    else:
        table(rows, ["id", "file_name", "title", ("product_id", "prod"), "product_name", ("is_primary", "primary"),
                     ("size_bytes", "bytes"), ("uploaded_by_name", "by"), "created_at"],
              title=f"{len(rows)} pictures", right=["id", "size_bytes"])


@images_app.command("download")
def download(
    ctx: typer.Context,
    ids: List[int] = typer.Argument(...),
    out: Path = typer.Option(Path("."), "--out", "-o", help="Target directory (or file name for a single id)."),
):
    """Download picture(s) from storage."""
    client = context.get(ctx).user()
    bucket = client.storage.from_(BUCKET)
    for pid in ids:
        pic = _picture(client, pid)
        data = bucket.download(pic["object_path"])
        if len(ids) == 1 and not out.is_dir() and out.suffix:
            target = out
        else:
            out.mkdir(parents=True, exist_ok=True)
            target = out / f"{pid}_{pic['file_name']}"
        target.write_bytes(data)
        ok(f"#{pid} → {target} ({len(data):,} bytes)")


@images_app.command("url")
def signed_url(ctx: typer.Context, pid: int, expires: int = typer.Option(3600, "--expires", "-e", help="Seconds.")):
    """Time-limited link to a picture (the bucket is private)."""
    client = context.get(ctx).user()
    pic = _picture(client, pid)
    res = client.storage.from_(BUCKET).create_signed_url(pic["object_path"], expires)
    url = res.get("signedURL") or res.get("signedUrl") if isinstance(res, dict) else getattr(res, "signed_url", res)
    console.print(url, soft_wrap=True)


@images_app.command("primary")
def make_primary(ctx: typer.Context, pid: int):
    """Make a picture the primary picture of its product."""
    context.get(ctx).user().rpc("set_primary_picture", {"p_picture_id": pid}).execute()
    ok(f"Picture #{pid} is now primary.")


@images_app.command("remove")
def remove(ctx: typer.Context, ids: List[int] = typer.Argument(...), yes: bool = typer.Option(False, "--yes", "-y")):
    """Delete picture(s): table row + storage object."""
    if not yes:
        typer.confirm(f"Delete picture(s) {', '.join(map(str, ids))}?", abort=True)
    delete_pictures(context.get(ctx).user(), ids)


def _walk(bucket, prefix: str = "") -> list[str]:
    """All object names in the bucket (Storage list() is one level deep)."""
    names, offset = [], 0
    while True:
        entries = bucket.list(prefix or None, {"limit": 1000, "offset": offset, "sortBy": {"column": "name", "order": "asc"}})
        for e in entries:
            full = f"{prefix}/{e['name']}" if prefix else e["name"]
            if e.get("id") is None:  # folder
                names += _walk(bucket, full)
            elif e["name"] != ".emptyFolderPlaceholder":
                names.append(full)
        if len(entries) < 1000:
            return names
        offset += 1000


@images_app.command("verify")
def verify(ctx: typer.Context, fix: bool = typer.Option(False, "--fix", help="Delete orphans on both sides.")):
    """Compare bucket objects with table rows; report (and optionally fix) mismatches."""
    client = context.get(ctx).user()
    bucket = client.storage.from_(BUCKET)
    objects = set(_walk(bucket))
    rows = {r["object_path"]: r["id"] for r in client.table("pictures").select("id,object_path").execute().data}
    orphan_objects = sorted(objects - rows.keys())
    missing = sorted(set(rows) - objects)
    info(f"{len(objects)} objects in bucket, {len(rows)} rows in table")
    for p in orphan_objects:
        warn(f"object without row: {p}")
    for p in missing:
        warn(f"row #{rows[p]} without object: {p}")
    if not orphan_objects and not missing:
        ok("Bucket and table are in sync.")
        return
    if fix:
        if orphan_objects:
            bucket.remove(orphan_objects)
        for p in missing:
            client.rpc("remove_picture", {"p_picture_id": rows[p]}).execute()
        ok(f"Fixed: removed {len(orphan_objects)} orphan object(s), {len(missing)} dangling row(s).")
    else:
        info("Run with --fix to clean up.")
