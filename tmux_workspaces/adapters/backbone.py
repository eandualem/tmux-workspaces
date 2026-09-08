"""Explicitly enabled read-only loopback Backbone metadata provider."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shlex
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..discovery import Snapshot, session_items


def valid_agent(name: str) -> bool:
    return isinstance(name, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name))


def connection(data_dir: Path, url: str | None) -> tuple[str, str]:
    # Only read this named secret. Never load/export the whole .env into tmux.
    secret = os.environ.get("BACKBONE_API_KEY", "")
    if not secret and (data_dir / ".env").exists():
        for line in (data_dir / ".env").read_text().splitlines():
            match = re.match(r"\s*(?:export\s+)?BACKBONE_API_KEY\s*=\s*(.*)", line)
            if match:
                values = shlex.split(match[1], comments=True)
                secret = values[0] if values else ""
    settings = {}
    db_path = data_dir / "backbone.db"
    if not url and db_path.exists():
        with contextlib.closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
            settings = {
                key: json.loads(value)
                for key, value in db.execute(
                    "SELECT key, value FROM settings "
                    "WHERE key IN ('backbone.host', 'backbone.port')"
                )
            }
    host = settings.get("backbone.host", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = os.environ.get("BACKBONE_PORT") or settings.get("backbone.port", 7120)
    url = url or f"http://{host}:{port}"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The Backbone adapter requires a local API. Run it on the tmux host.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a local API URL without credentials, query or fragment")
    return url.rstrip("/"), secret


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Backbone API redirects are not supported")


class BackboneProvider:
    def __init__(self, data_dir: Path, url: str | None = None):
        self.data_dir, self.url = data_dir, url
        self.last = Snapshot()

    def _fetch(self) -> dict[str, dict]:
        # Resolve the opt-in adapter lazily so bad configuration never prevents
        # normal terminal use. Retrying also recovers after configuration repairs.
        url, key = connection(self.data_dir, self.url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url + "/api/agents")
        if key:
            request.add_header("Authorization", "Bearer " + key)
        with opener.open(request, timeout=3) as response:
            items = json.load(response)["items"]
        roster = session_items(items, valid_agent, "backbone")
        for item in roster.values():
            # API agent state does not establish tmux attachment availability.
            item.pop("online", None)
        return roster

    def read(self) -> Snapshot:
        try:
            self.last = Snapshot(self._fetch(), observed_at=time.time())
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, http.client.HTTPException):
            self.last = self.last.unavailable("Backbone unavailable; states stale")
        return self.last
