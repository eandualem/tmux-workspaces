"""Own ordinary shell sessions separately from disposable viewer clients."""

import fcntl
import os
import re
import shlex
from pathlib import Path

from .tmux import SHELL_CONTEXT_NAMES, Tmux, shell_context


def shell_command(shell: str) -> str:
    # The library server outlives a viewer/SSH connection. Explicitly reset this
    # context, including missing values, rather than inheriting its stale globals.
    command = ["env", "-u", "TMUX", "-u", "TMUX_PANE"]
    for name in SHELL_CONTEXT_NAMES:
        command += ["-u", name]
    command += [f"{name}={value}" for name, value in shell_context().items()]
    return shlex.join([*command, shell, "-l", "-i"])


class Shells:
    """Persistent ordinary terminals, separate from both viewers and agents."""

    def __init__(self, socket: str):
        self.tmux = Tmux(socket)

    @staticmethod
    def name(pane: dict) -> str:
        if not re.fullmatch(r"[a-f0-9]{12}", pane["id"]):
            raise ValueError("Invalid terminal identity")
        return "terminal-" + pane["id"]

    def ensure(self, pane: dict) -> str:
        name = self.name(pane)
        # Two windows may open the same saved terminal simultaneously.
        with open(self.tmux.socket + ".viewer-lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if (
                self.tmux.run("list-sessions", "-F", "#{session_name}", check=False)
                .splitlines()
                .count(name)
            ):
                return name
            shell = os.environ.get("SHELL") or "/bin/sh"
            if not Path(shell).is_file():
                shell = "/bin/sh"
            cwd = pane.get("cwd") or str(Path.cwd())
            if not Path(cwd).is_dir():
                cwd = str(Path.home())
            # The viewer's private tmux identity must not redirect Backbone CLI
            # commands to this server. The shell still has a normal terminal.
            command = shell_command(shell)
            self.tmux.run("-f", "/dev/null", "new-session", "-d", "-s", name, "-c", cwd, command)
            self.tmux.run("set-option", "-t", "=" + name + ":", "status", "off")
            self.tmux.run("set-option", "-t", "=" + name + ":", "mouse", "on")
            self.tmux.run("set-window-option", "-t", "=" + name + ":", "remain-on-exit", "on")
        return name

    def remember(self, pane: dict) -> None:
        cwd = self.tmux.run(
            "display-message",
            "-p",
            "-t",
            "=" + self.name(pane) + ":",
            "#{pane_current_path}",
            check=False,
        )
        if cwd:
            pane["cwd"] = cwd

    def close(self, pane: dict) -> None:
        self.tmux.run("kill-session", "-t", "=" + self.name(pane) + ":", check=False)
