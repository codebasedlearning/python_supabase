# (C) A.Voß, a.voss@fh-aachen.de, info@codebasedlearning.dev
"""Per-invocation context: target, settings and lazily created Supabase clients."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import typer
from supabase import Client, ClientOptions, create_client
from supabase_auth.errors import AuthError

from sbcli import session
from sbcli.config import Settings, resolve_target
from sbcli.errors import CliError


def _options(auto_refresh: bool = False) -> ClientOptions:
    # No background refresh threads, no on-disk storage by the library:
    # sbcli persists the session itself (see session.py).
    return ClientOptions(auto_refresh_token=auto_refresh, persist_session=False)


@dataclass
class Ctx:
    target: str
    _settings: Settings | None = None
    _clients: dict = field(default_factory=dict)

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings.load(self.target)
        return self._settings

    # --- clients -------------------------------------------------------------
    def anon(self) -> Client:
        """Client with the public (anon/publishable) key and no user."""
        if "anon" not in self._clients:
            s = self.settings
            s.require("url", "anon_key")
            self._clients["anon"] = create_client(s.url, s.anon_key, options=_options())
        return self._clients["anon"]

    def user(self) -> Client:
        """Client acting as the signed-in user (RLS role `authenticated`)."""
        if "user" in self._clients:
            return self._clients["user"]
        data = session.load(self.target)
        if not data:
            raise CliError(f"Not logged in on target '{self.target}'. Run: sbcli auth login EMAIL")
        s = self.settings
        s.require("url", "anon_key")
        client = create_client(s.url, s.anon_key, options=_options())
        try:
            resp = client.auth.set_session(data["access_token"], data["refresh_token"])
        except AuthError as exc:
            session.clear(self.target)
            raise CliError(f"Session is no longer valid ({exc}). Please run: sbcli auth login EMAIL") from exc
        if not resp.session:
            session.clear(self.target)
            raise CliError("Session could not be refreshed. Please run: sbcli auth login EMAIL")
        session.save(self.target, resp.session)  # refresh tokens rotate — keep the newest
        self._clients["user"] = client
        return client

    def refresh_if_needed(self, client: Client, margin: int = 120) -> None:
        """For long-running loops (chat listen --poll)."""
        current = client.auth.get_session()
        if current and current.expires_at and current.expires_at - time.time() < margin:
            resp = client.auth.refresh_session()
            if resp.session:
                session.save(self.target, resp.session)

    def admin(self) -> Client:
        """Client with the service-role / secret key — bypasses RLS. Admin commands only."""
        if "admin" not in self._clients:
            s = self.settings
            s.require("url", "service_key")
            self._clients["admin"] = create_client(s.url, s.service_key, options=_options())
        return self._clients["admin"]

    def user_id(self) -> str:
        self.user()
        data = session.load(self.target) or {}
        return data.get("user_id")


def get(ctx: typer.Context) -> Ctx:
    """Fetch the Ctx created by the root callback (works from any sub-command)."""
    root = ctx.find_root()
    if not isinstance(root.obj, Ctx):
        root.obj = Ctx(target=resolve_target(None))
    return root.obj
