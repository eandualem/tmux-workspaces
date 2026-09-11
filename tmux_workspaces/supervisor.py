"""Supervise one viewer window at a time, each in its own fresh interpreter.

This process is deliberately small and stays loaded for the life of the command.
It owns the terminal, the canonical launch context and the decision to open a
replacement; everything else - preflight, settings, adapters, rendering - runs in
a window child that is started again from disk each time, so a refresh picks up
changed application code. A child never supervises its successor, and a
replacement needs an explicit request, a clean exit and a valid report.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import relaunch
from .cli import default_source_socket
from .entrypoints import application_argv
from .keymap import keymap_source
from .theme import theme_path

FORWARDED_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
# How long a window gets to finish its own cleanup once it has been asked to
# stop. Its work is closing a private server and removing its own directory.
STOP_GRACE = 10.0
REAP_GRACE = 5.0


class LaunchContext:
    """What this command asked for, captured before anything derives from it."""

    def __init__(self, args):
        if not args.backbone and (args.backbone_data_dir is not None or args.url is not None):
            raise ValueError("Use --backbone to enable Backbone configuration and API access")
        self.demo = bool(args.demo)
        self.base_library = args.data_dir.expanduser().resolve()
        # The disposable library lives beside the real one; a reopened command
        # must ask for the same mode rather than nest another directory.
        self.library = self.base_library / "demo" if self.demo else self.base_library
        self.source_socket = (
            None
            if self.demo
            else str(Path(args.source_socket or default_source_socket()).expanduser().resolve())
        )
        self.backbone = bool(args.backbone)
        self.backbone_dir = (
            (
                args.backbone_data_dir
                or Path(os.environ.get("BACKBONE_DATA_DIR") or "~/.local/share/agent-backbone")
            )
            .expanduser()
            .resolve()
            if self.backbone
            else None
        )
        self.url = args.url if self.backbone else None
        self.theme = theme_path(args.theme)
        # A background given on the command line reaches every window it
        # starts; otherwise each window asks the terminal itself.
        self.terminal_background = args.terminal_background
        self.shortcut_hints = args.shortcut_hints
        # A launcher-supplied snapshot fixes this window's keys and the terminal
        # profile generated from them, so such a window is reopened by hand.
        self.keymap_state = args.keymap_state
        self.no_keymap = bool(args.no_keymap)
        # Where the keys came from is part of the launch context even when this
        # window's own map is frozen: reopening has to name the same file.
        self.keymap_selector: Path | None = None
        self.keymap_required = False
        if not self.no_keymap:
            if args.keymap is None and args.keymap_source is not None:
                # A launcher resolved this selection in the environment that
                # made it. This process may not have that environment, so keep
                # the file it found instead of deriving a different one.
                self.keymap_selector = args.keymap_source
                self.keymap_required = bool(args.keymap_required)
            else:
                self.keymap_selector, self.keymap_required = keymap_source(args.keymap)
        self.ghostty_app = args.ghostty_app
        self.host_socket, self.host_pane = args.host_socket, args.host_pane
        if not self.host_socket:
            outer = os.environ.get("TMUX", "").rsplit(",", 2)
            if len(outer) == 3:
                self.host_socket = outer[0]
                self.host_pane = os.environ.get("TMUX_PANE")
        self.reopen = self._command(application_argv())
        self.published = self._published()

    def _command(self, argv: list[str]) -> str:
        """A command someone can run, including where its settings come from.

        Everything is one ordinary argument list: a setting that has to travel
        with it is carried by env rather than by shell assignment syntax, so the
        whole thing survives quoting and runs without a shell.
        """
        environment = self._environment()
        prefix = ["env", *environment] if environment else []
        return shlex.join([*prefix, *argv, *self._options()])

    def _environment(self) -> list[str]:
        """Name the configuration directory when this one would not find it.

        An optional default cannot be passed as an explicit file without making
        an absent one an error, so the directory that selects it is named
        instead, and only when resolving it here would choose another.
        """
        selector = self.keymap_selector
        if self.no_keymap or self.keymap_required or selector is None:
            return []
        if selector == keymap_source(None)[0]:
            return []
        if selector.name != "keymap.toml" or selector.parent.name != "tmux-workspaces":
            return []
        return [f"XDG_CONFIG_HOME={selector.parent.parent}"]

    def _options(self) -> list[str]:
        """The user-facing options that reproduce this library and adapter."""
        options = ["--data-dir", str(self.base_library), "--theme", str(self.theme)]
        if self.demo:
            options.append("--demo")
        elif self.source_socket:
            options += ["--source-socket", self.source_socket]
        if self.backbone:
            options += ["--backbone", "--backbone-data-dir", str(self.backbone_dir)]
            if self.url:
                options += ["--url", self.url]
        if self.no_keymap:
            options.append("--no-keymap")
        elif self.keymap_required:
            # An explicitly selected file, including one named by the
            # environment, may not be reachable the same way next time.
            options += ["--keymap", str(self.keymap_selector)]
        return options

    def _published(self) -> str | None:
        """The reopen text a fixed-profile window shows, or nothing to show."""
        if self.keymap_state is None:
            return self.reopen
        if self.shortcut_hints != "command":
            return self.reopen
        launcher = application_argv()[1]
        script = Path(launcher).resolve().parent / "ghostty" if launcher != "-m" else None
        if script is None or not script.is_file():
            # Only a source checkout ships the dedicated launcher; anything else
            # states the limitation instead of inventing a command.
            return None
        app = ["--ghostty-app", self.ghostty_app] if self.ghostty_app else []
        return self._command([str(script), *app])

    def child_args(self, handover: Path, selection: dict | None) -> list[str]:
        args = [
            "_window",
            "--data-dir",
            str(self.library),
            "--theme",
            str(self.theme),
            "--shortcut-hints",
            self.shortcut_hints,
            "--handover",
            str(handover),
            "--supervisor-pid",
            str(os.getpid()),
        ]
        if self.source_socket:
            args += ["--source-socket", self.source_socket]
        if self.demo:
            args.append("--demo")
        if self.backbone:
            args += ["--backbone", "--backbone-data-dir", str(self.backbone_dir)]
            if self.url:
                args += ["--url", self.url]
        if self.keymap_state is not None:
            args += ["--keymap-state", self.keymap_state, "--manual-reopen"]
        if self.no_keymap:
            args.append("--no-keymap")
        else:
            # Named even when the keys are frozen: the running map came from
            # this file, and it is the one the shortcut editor writes.
            args += ["--keymap-source", str(self.keymap_selector)]
            if self.keymap_required:
                args.append("--keymap-required")
        if self.host_socket and self.host_pane:
            args += ["--host-socket", self.host_socket, "--host-pane", self.host_pane]
        if self.published:
            args += ["--reopen-command", self.published]
        if self.ghostty_app:
            args += ["--ghostty-app", self.ghostty_app]
        if self.terminal_background:
            args += ["--terminal-background", self.terminal_background]
        carried = relaunch.navigation_argument(selection)
        if carried:
            args += ["--carry-navigation", carried]
        return args


class Interrupted(Exception):
    """Raised inside the wait so a bounded stop can start straight away."""


class Windows:
    """Own the window this launcher is running, and its share of the signals.

    The handlers are installed for the whole run, not around one wait, so a
    signal that arrives while a window is starting still reaches it instead of
    leaving it holding the terminal. Each signal is passed on at most once: the
    terminal already delivers to the whole group, and a second delivery can
    interrupt a window in the middle of its own cleanup.
    """

    def __init__(self, grace: float = STOP_GRACE):
        self.child: subprocess.Popen | None = None
        self.received: int | None = None
        self.forwarded: set[int] = set()
        self.grace = grace
        self.waiting = False
        self._previous: dict[int, object] = {}

    def __enter__(self):
        for number in FORWARDED_SIGNALS:
            with contextlib.suppress(OSError, ValueError):
                self._previous[number] = signal.signal(number, self._handle)
        return self

    def __exit__(self, *_exception):
        for number, handler in self._previous.items():
            with contextlib.suppress(OSError, ValueError):
                signal.signal(number, handler)

    def _handle(self, number, _frame):
        self.received = number
        self._forward(number)
        if self.waiting:
            # Waiting for a child is retried across signals, so leave it
            # deliberately; the stop from here on is a bounded one.
            raise Interrupted

    def _forward(self, number: int) -> None:
        if self.child is None or number in self.forwarded:
            return
        self.forwarded.add(number)
        with contextlib.suppress(OSError):
            self.child.send_signal(number)

    def adopt(self, child: subprocess.Popen) -> None:
        self.child = child
        if self.received is not None:
            self._forward(self.received)

    def wait(self) -> tuple[int, bool]:
        """Wait for this window's own cleanup, then say how it ended.

        A window that was asked to stop gets a bounded grace for that cleanup.
        One that keeps running is ended plainly rather than holding the terminal
        for ever, and what that costs is said out loud.
        """
        # A signal that arrived before this window was adopted has already been
        # passed on, so the grace for it starts here rather than never.
        deadline = None if self.received is None else time.monotonic() + self.grace
        while True:
            try:
                self.waiting = True
                code = self.child.wait(
                    timeout=None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                break
            except Interrupted:
                deadline = self._deadline(deadline)
            except KeyboardInterrupt:
                self.received = self.received or signal.SIGINT
                self._forward(signal.SIGINT)
                deadline = self._deadline(deadline)
            except subprocess.TimeoutExpired:
                code = self._force()
                break
            finally:
                self.waiting = False
        self.child = None
        return (128 + self.received, True) if self.received is not None else (code, False)

    def _deadline(self, deadline: float | None) -> float:
        """Repeats do not buy a stopping window more time."""
        return time.monotonic() + self.grace if deadline is None else deadline

    def _force(self) -> int:
        """End a window that did not stop, and say what that means."""
        print(
            f"Workspace viewer: window {self.child.pid} did not stop within "
            f"{self.grace:g} seconds; ending it now. Its own cleanup may be incomplete, "
            "and its display server closes when this terminal detaches.",
            file=sys.stderr,
            flush=True,
        )
        with contextlib.suppress(OSError):
            self.child.kill()
        try:
            return self.child.wait(timeout=REAP_GRACE)
        except subprocess.TimeoutExpired:
            return 128 + signal.SIGKILL


def supervise(args) -> int:
    """Run windows until one exits without asking for a replacement."""
    context = LaunchContext(args)
    selection, replacements, failure = None, 0, None
    with (
        tempfile.TemporaryDirectory(prefix="tmux-workspaces-windows-") as directory,
        Windows() as windows,
    ):
        while failure is None:
            handover = Path(directory) / f"window-{uuid.uuid4().hex}.json"
            try:
                child = subprocess.Popen(
                    [*application_argv(), *context.child_args(handover, selection)]
                )
            except OSError as error:
                if not replacements:
                    raise
                failure = f"the refreshed viewer could not start: {error}"
                break
            windows.adopt(child)
            code, signalled = windows.wait()
            status = relaunch.read_status(handover)
            handover.unlink(missing_ok=True)
            if signalled:
                # This launcher was asked to stop and its window has finished
                # cleaning up; the terminal is going away with it.
                return code
            if code or status is None or not status["ready"]:
                # A window that never came up, or ended without reporting its
                # own cleanup, is never replaced automatically.
                if not replacements:
                    # The first window has already explained itself on this
                    # terminal, exactly as it did before it was supervised.
                    return code or 1
                failure = (
                    f"the refreshed viewer exited with status {code}"
                    if code
                    else "the refreshed viewer closed before it finished starting"
                )
                break
            if not status["relaunch"]:
                return code
            selection, replacements = status["navigation"], replacements + 1
    # Outside the loop the handover directory is gone and the signal handlers
    # are this process's own again, so recovery can hand over the terminal.
    return relaunch.recover(failure, context.reopen)
