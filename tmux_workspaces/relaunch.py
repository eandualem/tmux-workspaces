"""One window's refresh request, and the recovery a failed replacement needs.

A refresh replaces the window that asked for it and nothing else. The request is
a small flag file inside that window's own instance directory, carrying
identifiers only, so it can never redirect cleanup or name a server to stop. The
supervisor learns the outcome from a bounded status file it owns, and the text
offered for reopening always comes from the launch context its parent captured,
never from a window's own private arguments.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .entrypoints import application_argv
from .keymap import load_keymap
from .model import leaves
from .preflight import check_tmux
from .tmux import clean_env

MARKER = "relaunch.json"
STARTED = "started"
MAX_REQUEST_BYTES = 4096
MAX_COMMAND_BYTES = 4096
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{0,64}")
# The whole confirmation must stay inside the shortcut acknowledgement window,
# so the probe is a short liveness check rather than a full startup.
PROBE_TIMEOUT = 2.0


def _read(path: Path, limit: int = MAX_REQUEST_BYTES) -> dict | None:
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError:
        return None
    if len(payload) > limit:
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def navigation(value) -> dict | None:
    """Accept identifiers only; a handover never carries a path or a command."""
    if not isinstance(value, dict):
        return None
    result = {}
    for field in ("workspace", "tab", "leaf"):
        item = value.get(field, "")
        if not isinstance(item, str) or not IDENTIFIER.fullmatch(item):
            return None
        result[field] = item
    if not result["workspace"]:
        return None
    result["focus"] = bool(value.get("focus", False))
    return result


def navigation_argument(selection: dict | None) -> str | None:
    """Bounded text a supervisor may hand its next window, or nothing."""
    checked = navigation(selection) if selection is not None else None
    return json.dumps(checked) if checked else None


def parse_navigation(text: str | None) -> dict | None:
    if not text or len(text.encode()) > MAX_REQUEST_BYTES:
        return None
    try:
        return navigation(json.loads(text))
    except ValueError:
        return None


def write_status(path: Path, *, ready: bool, relaunch: bool, selection=None) -> None:
    """Report this window's outcome after its own cleanup has finished."""
    status = {"ready": bool(ready), "relaunch": bool(relaunch)}
    checked = navigation(selection) if selection is not None else None
    if checked:
        status["navigation"] = checked
    with contextlib.suppress(OSError):
        Path(path).write_text(json.dumps(status))


def read_status(path: Path) -> dict | None:
    """A missing, damaged or incomplete report is not a clean exit."""
    status = _read(Path(path))
    if not status or not isinstance(status.get("ready"), bool):
        return None
    if not isinstance(status.get("relaunch"), bool):
        return None
    return {
        "ready": status["ready"],
        "relaunch": status["relaunch"],
        "navigation": navigation(status.get("navigation", {})),
    }


def recover(reason: str, command: str) -> int:
    """Leave a usable terminal behind, with the exact command to reopen.

    A dedicated surface closes when this process does, so printing instructions
    and exiting would take them away again. Hand the terminal to an ordinary
    shell instead; the library, its shells and any external sessions are
    untouched and the command above reopens the window.
    """
    print(
        f"Workspace viewer: {reason}\n"
        "Your library, its shells and any attached sessions are unchanged.\n"
        f"Reopen with: {command}",
        file=sys.stderr,
        flush=True,
    )
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 1
    shell = os.environ.get("SHELL") or ""
    if not shell or not Path(shell).is_file() or not os.access(shell, os.X_OK):
        shell = "/bin/sh"
    print(f"Starting {shell}; exit it to close this terminal.", file=sys.stderr, flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    with contextlib.suppress(OSError):
        # Become the shell rather than running one underneath a launcher that
        # has nothing left to do: the terminal is then an ordinary one.
        os.execv(shell, [shell, "-i"])
    return 1


class Request:
    """One viewer's own relaunch request, made at most once."""

    def __init__(
        self,
        instance_dir: Path,
        *,
        keymap_source: Path | None = None,
        keymap_required: bool = False,
        manual_reopen: bool = False,
        profile_hints: bool = False,
        nested: bool = False,
        reopen_command: str | None = None,
    ):
        self.instance_dir = Path(instance_dir)
        self.keymap_source = Path(keymap_source) if keymap_source else None
        self.keymap_required = keymap_required
        # An outer launcher fixed this window's keys and generated a terminal
        # profile from them. Reloading into that surface would leave the two
        # disagreeing, so such a window is only ever reopened by hand.
        self.manual_reopen = manual_reopen
        self.profile_hints = profile_hints
        self.nested = nested
        # Published by the supervisor from the context this library was opened
        # with. This window's own arguments are private and never shown.
        self.reopen_command = (
            reopen_command
            if reopen_command and len(reopen_command.encode()) <= MAX_COMMAND_BYTES
            else None
        )
        self.requested = False

    def notes(self) -> tuple[str, ...]:
        """State what a replacement applies, and what it truthfully cannot."""
        if self.manual_reopen:
            lines = [
                "This window's keys were fixed when its terminal opened.",
                "Refreshing here cannot change them.",
                "Leave this window running, then start it again with:"
                if self.reopen_command
                else "Reopen it with the launcher you used to start it.",
            ]
            return tuple(lines)
        lines = ["Reopens this window with current code and settings."]
        lines.append("Shells and attached sessions keep running.")
        lines.append(
            f"Reads {self.keymap_source.name} again."
            if self.keymap_source is not None
            else "Keys stay as this window started."
        )
        if self.nested:
            lines.append("The hosting tmux and its plugin are untouched.")
        return tuple(lines)

    def manual_command(self) -> str | None:
        """The command that opens this window again, as its launcher built it."""
        return self.reopen_command if self.manual_reopen else None

    def _launcher(self) -> str | None:
        """Prove the command each new window and pane runs still starts.

        A moved checkout, a removed package or an interpreter that no longer
        imports it must be found while this window is still usable. Reading the
        path is not enough: run it read-only, from this window's own directory
        and child environment, which is the context its panes get. It proves the
        entry point and command line only; later application modules are covered
        by the replacement's own startup report.
        """
        argv = application_argv()
        interpreter = Path(argv[0])
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            return f"Cannot reopen: {interpreter} is missing"
        if argv[1] != "-m" and not Path(argv[1]).is_file():
            return f"Cannot reopen: {argv[1]} is missing"
        try:
            probe = subprocess.run(
                [*argv, "--no-keymap", "--print-keymap", "toml"],
                env=clean_env(),
                capture_output=True,
                timeout=PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Cannot reopen: {argv[1]} did not answer in time"
        except (OSError, subprocess.SubprocessError):
            return f"Cannot reopen: {argv[1]} did not run"
        if probe.returncode:
            detail = probe.stderr.decode(errors="replace").strip().splitlines()
            return "Cannot reopen: " + (
                detail[-1][:120] if detail else f"{argv[1]} exited {probe.returncode}"
            )
        return None

    def check(self) -> str | None:
        """Report why replacing this viewer would fail, before anything closes.

        Called once, when a refresh is confirmed, so the whole check stays well
        inside the acknowledgement a shortcut waits for.
        """
        if self.manual_reopen:
            return "This window is reopened by hand"
        try:
            check_tmux()
        except RuntimeError as error:
            return str(error)
        problem = self._launcher()
        if problem:
            return problem
        if self.keymap_source is not None:
            if not self.keymap_source.exists():
                if self.keymap_required:
                    return f"keymap {self.keymap_source}: file does not exist"
            else:
                try:
                    load_keymap(self.keymap_source)
                except (ValueError, OSError) as error:
                    return str(error)
        if not os.access(self.instance_dir, os.W_OK | os.X_OK):
            return "Cannot record a refresh request for this window"
        return None

    def submit(self, selection: dict | None = None) -> bool:
        """Record the request once; duplicate confirmations change nothing."""
        if self.requested or self.manual_reopen:
            return False
        payload = {"relaunch": True}
        checked = navigation(selection) if selection is not None else None
        if checked is not None:
            payload["navigation"] = checked
        path = self.instance_dir / MARKER
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            return False
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(payload, stream)
        except OSError:
            with contextlib.suppress(OSError):
                path.unlink()
            return False
        self.requested = True
        return True


def take_request(instance_dir: Path) -> dict | None:
    """Consume this window's request, reading nothing outside its directory."""
    path = Path(instance_dir) / MARKER
    payload = _read(path)
    with contextlib.suppress(OSError):
        path.unlink()
    if not payload or payload.get("relaunch") is not True:
        return None
    return {"navigation": navigation(payload.get("navigation", {}))}


def started(instance_dir: Path) -> None:
    """Record that this window's navigation actually came up."""
    with contextlib.suppress(OSError):
        (Path(instance_dir) / STARTED).touch(mode=0o600)


def has_started(instance_dir: Path) -> bool:
    return (Path(instance_dir) / STARTED).exists()


def apply_navigation(state: dict, selection: dict | None) -> None:
    """Reselect identifiers that still exist, leaving everything else alone.

    The library keeps one navigation record, so without this a replacement would
    open wherever another window last looked. A workspace, tab or pane deleted
    meanwhile is simply not restored; a carried selection never invalidates the
    arrangement the replacement just loaded.
    """
    selection = navigation(selection) if selection else None
    if not selection:
        return
    space = next(
        (item for item in state.get("workspaces", []) if item["id"] == selection["workspace"]),
        None,
    )
    if space is None:
        return
    state["selected"] = space["id"]
    state["focus"] = selection["focus"]
    tab = next((item for item in space["tabs"] if item["id"] == selection["tab"]), None)
    if tab is None:
        return
    space["selected"] = tab["id"]
    if any(leaf["id"] == selection["leaf"] for leaf in leaves(tab["tree"])):
        tab["focus"] = selection["leaf"]
