"""Explicit-socket tmux commands and child environment policy."""

import os
import subprocess

SHELL_CONTEXT_NAMES = (
    "SSH_AUTH_SOCK",
    "XDG_CONFIG_HOME",
    "XDG_CONFIG_DIRS",
    "XDG_DATA_HOME",
    "XDG_DATA_DIRS",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
)


def shell_context() -> dict[str, str]:
    """Session paths carried to ordinary shells, never external tmux controls."""
    return {key: os.environ[key] for key in SHELL_CONTEXT_NAMES if key in os.environ}


def clean_env() -> dict[str, str]:
    # An allowlist prevents tokens and agent launch metadata entering the viewer server.
    names = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "LANG",
        "TMPDIR",
        "COLORTERM",
        "TERMINFO",
        "TERMINFO_DIRS",
    }
    return {
        key: value for key, value in os.environ.items() if key in names or key.startswith("LC_")
    }


class Tmux:
    def __init__(self, socket: str):
        self.socket = socket

    def run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["tmux", "-S", self.socket, *map(str, args)],
            env=clean_env(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if check and result.returncode:
            raise RuntimeError(result.stderr.strip() or "tmux command failed")
        return result.stdout.strip()
