# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""Configuration: targets (local / cloud), .env files, persisted CLI state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from sbcli.errors import CliError

TARGETS = ("local", "cloud")
BUCKET = "pictures"  # must match sql/04_pictures.sql

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # <repo>/src/sbcli -> <repo>


def config_dir() -> Path:
    return Path(os.environ.get("SBCLI_CONFIG_DIR", Path.home() / ".config" / "sbcli")).expanduser()


def project_root() -> Path:
    """Directory holding sql/ and the .env.<target> files."""
    env = os.environ.get("SBCLI_HOME")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "sql").is_dir():
        return cwd
    return _PACKAGE_ROOT


def sql_dir() -> Path:
    d = Path(os.environ.get("SBCLI_SQL_DIR", project_root() / "sql")).expanduser()
    if not d.is_dir():
        raise CliError(f"SQL directory not found: {d}  (set SBCLI_HOME or SBCLI_SQL_DIR)")
    return d


# --- persisted state (default target) ---------------------------------------
def _state_file() -> Path:
    return config_dir() / "state.json"


def read_state() -> dict:
    try:
        return json.loads(_state_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(**changes) -> None:
    state = read_state() | changes
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(state, indent=2))


def resolve_target(cli_value: str | None) -> str:
    target = cli_value or os.environ.get("SBCLI_TARGET") or read_state().get("target") or "local"
    if target not in TARGETS:
        raise CliError(f"Unknown target '{target}'. Use one of: {', '.join(TARGETS)}")
    return target


# --- settings ----------------------------------------------------------------
_ALIASES = {
    "url": ("SUPABASE_URL",),
    "anon_key": ("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY"),
    "service_key": ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY"),
    "db_url": ("DATABASE_URL", "SUPABASE_DB_URL"),
}

_HINTS = {
    "url": "SUPABASE_URL (Dashboard → Project Settings → API / `sbcli local env`)",
    "anon_key": "SUPABASE_ANON_KEY or SUPABASE_PUBLISHABLE_KEY",
    "service_key": "SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY (admin commands only!)",
    "db_url": "DATABASE_URL (Dashboard → Connect → Session pooler / `sbcli local env`)",
}


@dataclass
class Settings:
    target: str
    env_file: Path
    url: str | None = None
    anon_key: str | None = None
    service_key: str | None = None
    db_url: str | None = None
    sources: dict = field(default_factory=dict)

    @classmethod
    def load(cls, target: str) -> "Settings":
        env_file = project_root() / f".env.{target}"
        file_values = {k: v for k, v in dotenv_values(env_file).items() if v} if env_file.exists() else {}
        s = cls(target=target, env_file=env_file)
        for attr, names in _ALIASES.items():
            for name in names:
                # real environment wins over the file
                if os.environ.get(name):
                    setattr(s, attr, os.environ[name])
                    s.sources[attr] = f"env:{name}"
                    break
                if file_values.get(name):
                    setattr(s, attr, file_values[name])
                    s.sources[attr] = f"{env_file.name}:{name}"
                    break
        if s.url:
            s.url = s.url.rstrip("/")
        return s

    def require(self, *attrs: str) -> None:
        missing = [a for a in attrs if not getattr(self, a)]
        if missing:
            lines = "\n".join(f"  • {_HINTS[a]}" for a in missing)
            raise CliError(
                f"Missing configuration for target '{self.target}':\n{lines}\n"
                f"Put them into {self.env_file}"
                + ("  (or run `sbcli local env`)" if self.target == "local" else "")
            )


def mask(value: str | None, keep: int = 6) -> str:
    if not value:
        return "—"
    if "://" in value and "@" in value:  # postgres URL: hide the password
        scheme, rest = value.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:•••@{host}"
    return value[:keep] + "…" + value[-4:] if len(value) > keep + 4 else "•••"
