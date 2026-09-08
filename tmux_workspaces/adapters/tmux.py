"""Discover exact session names on one explicit tmux socket, using no writes."""

import subprocess
import time
from pathlib import Path

from ..discovery import Snapshot
from ..targets import valid_session
from ..tmux import clean_env


class TmuxProvider:
    def __init__(self, socket: str):
        self.socket = socket

    def read(self) -> Snapshot:
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
                return (
                    Snapshot(observed_at=time.time())
                    if absent
                    else Snapshot(error="Session list unavailable", stale=True)
                )
            return Snapshot(
                {
                    name: {"name": name, "state": "running", "online": True, "origin": "tmux"}
                    for name in result.stdout.split("\n")
                    if valid_session(name)
                },
                observed_at=time.time(),
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            return Snapshot(error="Session list unavailable", stale=True)
