"""Real mouse/PTY exercise against disposable demo shells, never real agents."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import select
import signal
import sqlite3
import struct
import subprocess
import sys
import termios
import time
from contextlib import closing
from pathlib import Path

from tmux_workspaces.model import Model
from tmux_workspaces.tmux import clean_env


class Client:
    def __init__(self, arguments: list[str], cols=160, rows=38, terminal_env=None, launcher=None):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = clean_env() | {"TERM": "xterm-256color", "LANG": "en_US.UTF-8", "SHELL": "/bin/sh"}
        env.update(terminal_env or {})
        self.process = subprocess.Popen(
            [
                *(launcher or [sys.executable, str(Path(__file__).resolve().parents[2] / "run")]),
                *arguments,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
        )
        os.close(slave)
        self.output = b""

    def manifest(self, directory: Path) -> Path | None:
        for path in (directory / "windows").glob("*/runtime.json"):
            try:
                if json.loads(path.read_text())["pid"] == self.process.pid:
                    return path
            except (FileNotFoundError, ValueError):
                pass
        return None

    def pump(self, seconds=0.15):
        if self.master is None:
            time.sleep(seconds)
            return
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if select.select([self.master], [], [], 0.03)[0]:
                try:
                    self.output += os.read(self.master, 65536)
                except OSError:
                    break

    def click(self, x, y):
        os.write(self.master, f"\x1b[<0;{x};{y}M".encode())
        self.pump(0.08)
        try:
            os.write(self.master, f"\x1b[<0;{x};{y}m".encode())
        except OSError as exc:
            # Clicking Exit can close the terminal before the physical release.
            if exc.errno != errno.EIO:
                raise
        self.pump(0.3)

    def type(self, text):
        os.write(self.master, text.encode())
        self.pump(0.25)

    def resize(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(self.process.pid, signal.SIGWINCH)
        self.pump(1)

    def close(self):
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGTERM)
            self.pump(0.3)
            self.process.wait(timeout=10)
        if self.master is not None:
            os.close(self.master)
            self.master = None

    def close_terminal(self):
        os.close(self.master)
        self.master = None
        self.process.wait(timeout=10)


def wait(client, predicate, description, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        client.pump(0.1)
    raise AssertionError(description + "\n" + client.output[-3000:].decode(errors="replace"))


def saved(directory: Path) -> Model:
    with closing(sqlite3.connect(directory / "layouts.db")) as db:
        return Model(
            json.loads(db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()[0])
        )


def click_attach(client, viewer, leaf_id):
    # Click an inactive terminal, then immediately open the sidebar chooser.
    # pane_last must preserve that target even before the sidebar poll runs.
    def target():
        return next(
            (
                line.split("|")
                for line in viewer.run(
                    "list-panes", "-F", "#{pane_left}|#{pane_top}|#{@viewer_leaf_id}"
                ).splitlines()
                if line.split("|")[-1] == leaf_id
            ),
            None,
        )

    wait(client, target, "destination shell pane did not appear")
    pane = target()
    client.click(int(pane[0]) + 2, int(pane[1]) + 1)
    click_button(client, viewer, "Attach session…")
    wait(
        client,
        lambda: "Attach to selected pane" in viewer.run("capture-pane", "-p", "-t", "%0"),
        "sidebar did not open attachment chooser",
    )


def click_button(client, viewer, text):
    if text == "+ Tab":
        text = "[ + ]"

    def sidebar():
        return viewer.run("capture-pane", "-p", "-t", "%0")

    wait(client, lambda: text in sidebar(), "missing button: " + text)
    lines = sidebar().splitlines()
    row = next(i for i, line in enumerate(lines) if text in line)
    top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
    client.click(lines[row].index(text) + 2, row + top + 1)
