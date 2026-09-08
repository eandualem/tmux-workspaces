"""Commands that stay importable after tmux clears the parent environment."""

import shlex
import sys
from pathlib import Path


def application_argv(*args: str) -> list[str]:
    root = Path(__file__).resolve().parent.parent
    launcher = root / "run"
    if launcher.is_file() and (root / "pyproject.toml").is_file():
        return [sys.executable, str(launcher), *args]
    return [sys.executable, "-m", "tmux_workspaces", *args]


def script_command(*args: str) -> str:
    return shlex.join(application_argv(*args))
