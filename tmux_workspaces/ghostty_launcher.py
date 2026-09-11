#!/usr/bin/env python3
"""Start a separate macOS Ghostty instance using this checkout's shortcut profile."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .cli import default_source_socket, effective_keymap
from .cli import parser as viewer_parser
from .keymap import keymap_source

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = Path("/Applications/Ghostty.app")


def launch_command(
    arguments: list[str],
    app: Path = DEFAULT_APP,
    root: Path = ROOT,
    cwd: Path | None = None,
) -> list[str]:
    """Build a reviewable launch command without opening apps or changing files."""
    from .theme import theme_path

    cwd = (cwd or Path.cwd()).resolve()
    options = viewer_parser().parse_args(arguments)
    keymap = effective_keymap(options, cwd=cwd)
    data_dir = options.data_dir.expanduser()
    if not data_dir.is_absolute():
        data_dir = cwd / data_dir
    source = Path(options.source_socket or default_source_socket()).expanduser()
    if not source.is_absolute():
        source = cwd / source
    # Freeze the invoking shell's library/socket selection for Cmd-N too.
    # LaunchServices doesn't reliably inherit custom variables such as
    # TMUX_WORKSPACES_DATA_DIR or TMUX into a new graphical app instance.
    viewer_args = [
        *arguments,
        "--data-dir",
        str(data_dir.resolve()),
        "--source-socket",
        str(source.resolve()),
        "--shortcut-hints=command",
        "--keymap-state",
        keymap.to_toml(),
        "--theme",
        str(theme_path(path=options.theme, cwd=cwd)),
    ]
    # Keys are frozen for this instance, but where they came from still matters:
    # a viewer here can only be reopened through this launcher, and the command
    # it offers has to name the same file and application again.
    if not options.no_keymap:
        selected, explicit = keymap_source(options.keymap, cwd=cwd)
        # An application started this way does not inherit the environment that
        # chose the file, so name it either way: explicitly when it was asked
        # for, and as an optional default otherwise.
        viewer_args += ["--keymap" if explicit else "--keymap-source", str(selected)]
    if app.expanduser().resolve() != DEFAULT_APP:
        viewer_args += ["--ghostty-app", str(app.expanduser().resolve())]
    if options.backbone:
        backbone_dir = (
            options.backbone_data_dir
            or Path(os.environ.get("BACKBONE_DATA_DIR") or "~/.local/share/agent-backbone")
        ).expanduser()
        if not backbone_dir.is_absolute():
            backbone_dir = cwd / backbone_dir
        viewer_args += ["--backbone-data-dir", str(backbone_dir.resolve())]
    # Ghostty wraps shell commands in its own exec ("exec -l" on macOS).
    # Supplying another exec makes bash try to execute a program named "exec".
    command = "shell:" + shlex.join([sys.executable, str(root / "run"), *viewer_args])
    # Ghostty's command applies to every new surface; -e only affects the first.
    # Keep default config loading enabled for the owner's existing appearance.
    # Do not set window-save-state: on macOS it writes shared UserDefaults.
    return [
        "open",
        "-na",
        str(app.expanduser().resolve()),
        "--env",
        "PATH=" + os.environ.get("PATH", os.defpath),
        "--args",
        *[
            "--keybind=" + line.removeprefix("keybind = ")
            for line in keymap.ghostty_bindings().splitlines()
            if line.startswith("keybind = ")
        ],
        "--working-directory=" + str(cwd),
        "--mouse-reporting=true",
        "--shell-integration=none",
        # The window height is rarely a whole number of rows; extending each
        # edge cell's color into that remainder lets the panel and the
        # terminals reach the window edge instead of leaving a bare strip.
        "--window-padding-color=extend",
        "--quit-after-last-window-closed=true",
        "--command=" + command,
    ]


def check_launch(app: Path = DEFAULT_APP) -> None:
    """Check availability without creating a library or launching the native app."""
    from .preflight import check_tmux, load_curses

    if sys.platform != "darwin":
        raise RuntimeError(
            "The Ghostty launcher requires macOS; use ./run in your current terminal."
        )
    binary = app.expanduser() / "Contents/MacOS/ghostty"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(
            "Ghostty.app is missing or not executable; pass --ghostty-app PATH "
            "or use ./run in your current terminal."
        )
    check_tmux()
    load_curses()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ghostty",
        description=__doc__,
        epilog=(
            "Other arguments are passed to ./run, for example --data-dir PATH or --backbone. "
            "Use ./run --help for viewer options. This reads the normal Ghostty appearance "
            "and adds Command shortcuts only to the new instance; it does not edit or reload "
            "your Ghostty configuration. Native Cmd-N opens another workspace viewer. "
            "Ghostty instances still share macOS application preferences and restoration state."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="print the launch command and exit")
    parser.add_argument(
        "--ghostty-app",
        type=Path,
        default=DEFAULT_APP,
        metavar="PATH",
        help="Ghostty .app location",
    )
    options, viewer_args = parser.parse_known_args(arguments)
    if viewer_args[:1] == ["--"]:
        viewer_args = viewer_args[1:]
    try:
        if not options.dry_run:
            check_launch(options.ghostty_app)
        command = launch_command(viewer_args, options.ghostty_app)
    except (RuntimeError, ValueError, OSError) as error:
        parser.error(str(error))
    if options.dry_run:
        print(shlex.join(command))
        return 0
    try:
        result = subprocess.call(command)
        if result:
            print(
                f"Ghostty launch failed (exit {result}); use ./run in your current terminal.",
                file=sys.stderr,
            )
        return result
    except OSError as error:
        print(
            "Ghostty launch failed: " + str(error) + "; use ./run in your current terminal.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
