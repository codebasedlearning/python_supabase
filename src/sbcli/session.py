# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""Persist the signed-in user's tokens per target (~/.config/sbcli/sessions/<target>.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sbcli.config import config_dir


def _path(target: str) -> Path:
    return config_dir() / "sessions" / f"{target}.json"


def load(target: str) -> dict | None:
    try:
        return json.loads(_path(target).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(target: str, session) -> None:
    """Store a supabase_auth Session object."""
    data = {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": session.expires_at,
        "user_id": session.user.id if session.user else None,
        "email": session.user.email if session.user else None,
    }
    p = _path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    # create with 0600 — these are bearer tokens
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)


def clear(target: str) -> bool:
    try:
        _path(target).unlink()
        return True
    except FileNotFoundError:
        return False
