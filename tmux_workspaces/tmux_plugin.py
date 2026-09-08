#!/usr/bin/env python3
"""Bind a tmux key to the standalone workspace viewer without changing pane options."""

import argparse
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _tmux(socket: str, *args: str) -> str:
    return subprocess.run(
        ["tmux", "-S", socket, *args], check=True, capture_output=True, text=True
    ).stdout.removesuffix("\n")


@lru_cache(maxsize=32)
def _escapes_dollars(socket: str) -> bool:
    # Some tmux versions escape dollars in cmdq_print even without quoting.
    # Probe behavior instead of guessing versions (distributions carry patches).
    marker = "$tmux_workspaces_probe"
    return _tmux(socket, "display-message", "-p", "-l", marker) == "\\" + marker


def tmux(socket: str, *args: str) -> str:
    value = _tmux(socket, *args)
    if r"\$" in value and _escapes_dollars(socket):
        # Remove only the extra slash added by historical utf8_strvis. Literal
        # slashes, shell syntax and control-character spellings stay untouched.
        value = re.sub(r"\\\$(?=[A-Za-z_{])", "$", value)
    return value


def option(socket: str, name: str) -> str:
    return tmux(socket, "show-options", "-gqv", "@tmux-workspaces-" + name)


def install() -> None:
    # TPM and run-shell supply TMUX. Refuse to modify an implicit default server
    # when someone executes the entry directly from an ordinary terminal.
    context = os.environ.get("TMUX", "")
    if not context:
        raise ValueError("Load tmux-workspaces.tmux through tmux run-shell or TPM")
    socket = context.rsplit(",", 2)[0]
    socket = tmux(socket, "display-message", "-p", "#{socket_path}")
    key = option(socket, "key") or "W"
    tmux(
        socket,
        "bind-key",
        "-T",
        "prefix",
        "--",
        key,
        "new-window",
        "-n",
        "workspaces",
        "-c",
        "#{pane_current_path}",
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts/tmux_plugin.py"),
        "launch",
        "--source-socket",
        socket,
    )


def launch(socket: str) -> None:
    # Pass separate argv to exec and to new-window. Option values are never shell
    # code, including whitespace, quotes and shell metacharacters in data paths.
    command = [str(Path(__file__).resolve().parents[1] / "run"), "--source-socket", socket]
    host_pane = os.environ.get("TMUX_PANE")
    if host_pane:
        command += ["--host-socket", socket, "--host-pane", host_pane]
    data_dir = option(socket, "data-dir")
    if data_dir:
        command += ["--data-dir", data_dir]
    if option(socket, "backbone").strip().lower() in {"1", "on", "true", "yes"}:
        command += ["--backbone"]
        for name in ("backbone-data-dir", "url"):
            value = option(socket, name)
            if value:
                command += ["--" + name, value]
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    os.execve(command[0], command, env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("install")
    launch_parser = subparsers.add_parser("launch")
    launch_parser.add_argument("--source-socket", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "install":
            install()
        else:
            launch(args.source_socket)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        print("tmux-workspaces: " + detail.strip(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
