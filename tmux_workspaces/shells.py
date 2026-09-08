"""Own ordinary shell sessions separately from disposable viewer clients."""

import fcntl
import os
import re
import shlex
import uuid
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
        return self.ensure_many([pane])[pane["id"]]

    def ensure_many(self, panes: list[dict]) -> dict[str, str]:
        names = {pane["id"]: self.name(pane) for pane in panes}
        if not names:
            return names
        # Two windows may open the same saved terminal simultaneously.
        with open(self.tmux.socket + ".viewer-lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = set(
                self.tmux.run("list-sessions", "-F", "#{session_name}", check=False).splitlines()
            )
            for pane in panes:
                name = names[pane["id"]]
                if name not in existing:
                    self._create(pane, name)
                    existing.add(name)
        return names

    def _create(self, pane: dict, name: str) -> None:
        shell = os.environ.get("SHELL") or "/bin/sh"
        if not Path(shell).is_file():
            shell = "/bin/sh"
        cwd = pane.get("cwd") or str(Path.cwd())
        if not Path(cwd).is_dir():
            cwd = str(Path.home())
        # Ordinary commands must not inherit this private server's identity.
        command = shell_command(shell)
        self.tmux.run("-f", "/dev/null", "new-session", "-d", "-s", name, "-c", cwd, command)
        target = "=" + name + ":"
        self.tmux.batch(
            [
                ["set-option", "-t", target, "status", "off"],
                ["set-option", "-t", target, "mouse", "on"],
                ["set-window-option", "-t", target, "remain-on-exit", "on"],
            ]
        )

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

    def remember_many(self, panes: list[dict]) -> None:
        if not panes:
            return
        # One client, with explicit framing: directories can contain newlines.
        # On an incomplete response or marker collision use individual reads.
        marker = "__tw_cwd_" + uuid.uuid4().hex + "__"
        commands = [
            [
                "display-message",
                "-p",
                "-t",
                "=" + self.name(pane) + ":",
                "#{pane_current_path}" + marker,
            ]
            for pane in panes
        ]
        parts = self.tmux.batch(commands, check=False).split(marker)
        if len(parts) != len(panes) + 1 or parts[-1]:
            for pane in panes:
                self.remember(pane)
            return
        for index, pane in enumerate(panes):
            cwd = parts[index].removeprefix("\n") if index else parts[index]
            if cwd:
                pane["cwd"] = cwd

    def close(self, pane: dict) -> None:
        self.tmux.run("kill-session", "-t", "=" + self.name(pane) + ":", check=False)
