"""Read generic tmux sessions, with an explicitly enabled Backbone overlay."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import shlex
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from targets import valid_session
from terminal import clean_env


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


class Source:
    def __init__(
        self,
        socket: str,
        *,
        backbone_data_dir: Path | None = None,
        url: str | None = None,
        demo: Path | None = None,
    ):
        self.socket = socket
        self.backbone_data_dir = backbone_data_dir
        self.url = url
        self.demo = demo
        self.agents: dict[str, dict] = {}
        self.roster: dict[str, dict] = {}
        self.error = ""
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    @staticmethod
    def _items(items: object, *, backbone: bool) -> dict[str, dict]:
        if not isinstance(items, list):
            raise ValueError("Invalid session list")
        valid_name = valid_agent if backbone else valid_session
        result = {}
        for item in items:
            if (
                not isinstance(item, dict)
                or not valid_name(item.get("name"))
                or not item.get("configured", True)
            ):
                continue
            item = dict(item)
            item["origin"] = "backbone" if backbone else "demo"
            if not isinstance(item.get("state"), str):
                item["state"] = "unknown"
            result[item["name"]] = item
        return result

    def _sessions(self) -> tuple[dict[str, dict], str]:
        try:
            result = subprocess.run(
                ["tmux", "-S", self.socket, "list-sessions", "-F", "#{session_name}"],
                env=clean_env(),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode:
                # A fresh installation need not have an external tmux server.
                absent = not Path(self.socket).exists() or "no server running" in result.stderr
                return {}, "" if absent else "Session list unavailable"
            return {
                name: {"name": name, "state": "running", "online": True, "origin": "tmux"}
                for name in result.stdout.split("\n")
                if valid_session(name)
            }, ""
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return {}, "Session list unavailable"

    def _backbone(self) -> dict[str, dict]:
        # Resolve the opt-in adapter lazily so bad configuration never prevents
        # normal terminal use. Retrying also recovers after configuration repairs.
        url, key = connection(self.backbone_data_dir, self.url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url + "/api/agents")
        if key:
            request.add_header("Authorization", "Bearer " + key)
        with opener.open(request, timeout=3) as response:
            items = json.load(response)["items"]
        return self._items(items, backbone=True)

    def refresh(self) -> None:
        if self.demo:
            try:
                agents = self._items(json.loads(self.demo.read_text()), backbone=False)
            except (OSError, ValueError, TypeError):
                with self.lock:
                    self.error = "Demo list unavailable; states stale"
                return
            with self.lock:
                self.agents, self.error = agents, ""
            return

        agents, error = self._sessions()
        if self.backbone_data_dir is not None:
            try:
                self.roster = self._backbone()
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                sqlite3.Error,
                http.client.HTTPException,
            ):
                error = "; ".join(filter(None, (error, "Backbone unavailable; states stale")))
            for name, item in self.roster.items():
                # Availability describes attachment, not Backbone's agent state.
                agents[name] = {**item, "online": name in agents}
        with self.lock:
            self.agents, self.error = agents, error

    def snapshot(self) -> tuple[dict[str, dict], str]:
        with self.lock:
            return dict(self.agents), self.error

    def start(self) -> None:
        def poll():
            while not self.stop_event.wait(5):
                self.refresh()

        threading.Thread(target=poll, daemon=True, name="workspace-sessions").start()

    def close(self) -> None:
        self.stop_event.set()
