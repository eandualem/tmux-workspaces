"""Read-only startup checks; never connect to a tmux server or initialize a screen."""

from __future__ import annotations

import importlib
import os
import re
import shutil
import subprocess
import sys

from .bootstrap import require_python


def check_tmux() -> str:
    binary = shutil.which("tmux")
    if binary is None:
        raise RuntimeError(
            "tmux not found; tmux 3.3 or newer is required. Install it on this host."
        )
    try:
        result = subprocess.run([binary, "-V"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "Cannot run tmux -V; tmux 3.3 or newer is required. Check the tmux executable in PATH."
        ) from error
    version = result.stdout.strip()
    # Release patch letters, release candidates and next-X.Y development builds
    # keep the major/minor contract. An unnumbered build cannot establish a floor.
    match = re.fullmatch(r"tmux (?:next-)?(\d+)\.(\d+)(?:[a-zA-Z][\w.-]*|[-+][\w.-]+)?", version)
    if result.returncode or match is None:
        raise RuntimeError(
            f"Cannot verify tmux version {version!r}; tmux 3.3 or newer is required. "
            "Install a numbered build and check tmux -V."
        )
    if tuple(map(int, match.groups())) < (3, 3):
        raise RuntimeError(f"{version} detected; tmux 3.3 or newer is required. Upgrade tmux.")
    return version


def load_curses():
    try:
        module = importlib.import_module("curses")
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "Python curses support is unavailable; install Python 3.11 or newer built with "
            "ncurses support, then run the viewer with that interpreter."
        ) from error
    required = (
        "setupterm",
        "tigetnum",
        "tigetstr",
        "wrapper",
        "start_color",
        "use_default_colors",
        "init_pair",
        "mousemask",
        "getmouse",
    )
    if any(not callable(getattr(module, name, None)) for name in required):
        raise RuntimeError(
            "Python curses lacks required terminal/color/mouse support. "
            "Use Python 3.11 or newer built with ncurses support."
        )
    return module


def check_terminal() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError(
            "Run the viewer in an interactive terminal with terminal input and output."
        )
    terminal = os.environ.get("TERM", "")
    if not terminal.strip():
        raise RuntimeError(
            "TERM is missing; launch from a terminal or SSH session with its correct TERM value."
        )
    curses = load_curses()
    try:
        # setupterm reads terminfo without entering curses mode or emitting output.
        # The UI runs in a separate process, so its screen state remains untouched.
        curses.setupterm(term=terminal, fd=sys.stdout.fileno())
    except (curses.error, OSError, ValueError) as error:
        raise RuntimeError(
            f"TERM={terminal!r} has no usable terminfo entry. Install its terminfo on this host "
            "(including the SSH host), or use a terminal with an installed matching entry."
        ) from error
    colors, pairs = curses.tigetnum("colors"), curses.tigetnum("pairs")
    if colors < 8 or pairs < 5:
        raise RuntimeError(
            f"TERM={terminal!r} reports {max(colors, 0)} colors and {max(pairs, 0)} color pairs; "
            "at least 8 colors and 5 pairs are required. Use a color-capable terminal "
            "with matching terminfo."
        )
    if any(not curses.tigetstr(name) for name in ("cup", "clear", "sgr0")):
        raise RuntimeError(
            f"TERM={terminal!r} lacks cursor positioning, screen clearing or attribute reset. "
            "Use a full-screen terminal with matching terminfo."
        )


def check_startup() -> None:
    require_python()
    check_tmux()
    check_terminal()
