"""A workspace layer for tmux. Named purposes, tabs and saved split arrangements."""

import argparse
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path


def default_data_dir() -> Path:
    configured = os.environ.get("TMUX_WORKSPACES_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    root = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser()
    return root / "tmux-workspaces"


def default_source_socket() -> str:
    # Capture the invoking tmux server before private child environments clear TMUX.
    identity = os.environ.get("TMUX", "").rsplit(",", 2)
    if len(identity) == 3 and identity[0]:
        return identity[0]
    runtime_dir = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return str(Path(runtime_dir) / f"tmux-{os.getuid()}" / "default")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="tmux-workspaces", description=__doc__)
    result.add_argument(
        "mode",
        nargs="?",
        choices=["_window", "_sidebar", "_leaf", "_action"],
        help=argparse.SUPPRESS,
    )
    adapters = result.add_mutually_exclusive_group()
    adapters.add_argument("--demo", action="store_true", help="use disposable demo shells")
    adapters.add_argument(
        "--backbone", action="store_true", help="enable the read-only local Backbone adapter"
    )
    result.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="saved library (default: $XDG_DATA_HOME/tmux-workspaces "
        "or ~/.local/share/tmux-workspaces)",
    )
    result.add_argument(
        "--backbone-data-dir",
        type=Path,
        help="Backbone config directory; requires --backbone",
    )
    result.add_argument("--url", help="local Backbone API URL; requires --backbone")
    result.add_argument(
        "--source-socket",
        help="tmux socket for attachment chooser (default: invoking server or default)",
    )
    result.add_argument(
        "--shortcut-hints",
        choices=["prefix", "command"],
        default="prefix",
        help="shortcut labels to show (Command keys require the Ghostty launch profile)",
    )
    keymap = result.add_mutually_exclusive_group()
    keymap.add_argument(
        "--keymap", type=Path, help="TOML viewer keymap (overrides environment/default)"
    )
    keymap.add_argument("--no-keymap", action="store_true", help="use only the shipped keymap")
    result.add_argument(
        "--print-keymap",
        choices=["toml", "help", "ghostty"],
        help="print the effective configuration, shortcut help or Ghostty profile and exit",
    )
    # Transport the already validated map across tmux/LaunchServices environments.
    # New surfaces in the same Ghostty instance must match its fixed profile.
    result.add_argument("--keymap-state", help=argparse.SUPPRESS)
    result.add_argument(
        "--theme", type=Path, help="viewer color TOML (overrides environment/default path)"
    )
    result.add_argument("--terminal-colors", type=int, help=argparse.SUPPRESS)
    # A refresh re-reads the selected file before it replaces the viewer, so the
    # running instance validates exactly what the launcher will load next.
    result.add_argument("--keymap-source", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--keymap-required", action="store_true", help=argparse.SUPPRESS)
    # Set for a window whose map came fixed from an outer launcher, together
    # with a terminal profile generated from it: such a window is reopened,
    # never replaced in place.
    result.add_argument("--manual-reopen", action="store_true", help=argparse.SUPPRESS)
    # Written by one supervised window after its own cleanup, and read only by
    # the supervisor that named it.
    result.add_argument("--handover", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--supervisor-pid", type=int, default=0, help=argparse.SUPPRESS)
    # Advisory text and selection built by the supervisor; never executed here.
    result.add_argument("--reopen-command", help=argparse.SUPPRESS)
    result.add_argument("--carry-navigation", help=argparse.SUPPRESS)
    result.add_argument("--ghostty-app", help=argparse.SUPPRESS)
    result.add_argument("--viewer-socket", help=argparse.SUPPRESS)
    result.add_argument("--instance-dir", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--agent", default="", help=argparse.SUPPRESS)
    result.add_argument("--terminal", default="", help=argparse.SUPPRESS)
    result.add_argument("--shell-socket", help=argparse.SUPPRESS)
    result.add_argument("--action-socket", help=argparse.SUPPRESS)
    result.add_argument("--action", help=argparse.SUPPRESS)
    result.add_argument("--wait-action", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--host-socket", help=argparse.SUPPRESS)
    result.add_argument("--host-pane", help=argparse.SUPPRESS)
    return result


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def install_stop_signals() -> None:
    """Leave the terminal cleanly, and let the cleanup that follows finish.

    Both a launcher and its window stop this way, the window by running its own
    cleanup first. Once that has begun, every signal handled here is ignored: a
    second copy of the same one, or a different one arriving behind it, must not
    cut the cleanup short.
    """

    def stop(number, _frame):
        for managed in STOP_SIGNALS:
            with contextlib.suppress(OSError, ValueError):
                signal.signal(managed, signal.SIG_IGN)
        sys.exit(128 + number)

    for managed in STOP_SIGNALS:
        with contextlib.suppress(OSError, ValueError):
            signal.signal(managed, stop)


def effective_keymap(args, *, cwd: Path | None = None):
    from .keymap import DEFAULT_KEYMAP, Keymap, load_keymap

    if args.keymap_state is not None:
        import tomllib

        if len(args.keymap_state.encode()) > 65536:
            raise ValueError("Keymap snapshot exceeds 64 KiB")
        return Keymap.from_dict(tomllib.loads(args.keymap_state))
    source = getattr(args, "keymap_source", None)
    if source is not None and args.keymap is None and not args.no_keymap:
        # A supervisor resolved the selection once, from its own directory and
        # environment. Read that exact file, and keep an absent optional
        # default optional so one created later is still picked up.
        if args.keymap_required or source.exists():
            return load_keymap(source)
        return DEFAULT_KEYMAP
    return load_keymap(args.keymap, disabled=args.no_keymap, cwd=cwd)


def keymap_help(keymap) -> str:
    lines = [f"Viewer prefix: {keymap.prefix}", "Prefix, then key:"]
    lines += [f"  {keys:<20} {label}" for keys, label in keymap.prefix_help_rows()]
    lines += [
        "  Escape               Cancel prefix",
        f"  {keymap.prefix:<20} Send literal prefix",
        "",
        "Terminal profile triggers (require ./ghostty or terminal configuration):",
    ]
    lines += [f"  {keys:<20} {label}" for keys, label in keymap.direct_help_rows()]
    lines += [
        "",
        "The terminal consumes these triggers and sends stable CSI action codes to tmux.",
        "Keys intercepted by your OS or hosting tmux never reach the viewer.",
        "Print --print-keymap ghostty for the effective trigger-to-CSI profile.",
        "Use arrows and Enter in choosers and menus; see docs/SHORTCUTS.md.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parser().parse_args()
    try:
        if args.mode == "_action":
            from .controls import send_action

            send_action(args.action_socket, args.action, wait=args.wait_action)
            return 0
        if args.mode == "_leaf":
            from .attachments import leaf_main

            return leaf_main(args)
        if args.print_keymap:
            keymap = effective_keymap(args)
            text = {
                "toml": keymap.to_toml,
                "help": lambda: keymap_help(keymap),
                "ghostty": keymap.ghostty_bindings,
            }[args.print_keymap]()
            print(text, end="")
            return 0
        if args.mode == "_sidebar":
            from .application import sidebar_main

            return sidebar_main(args)

        install_stop_signals()
        if args.mode == "_window":
            from .application import window_main

            return window_main(args)
        from .supervisor import supervise

        return supervise(args)
    except subprocess.CalledProcessError as exc:
        detail = (
            exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        )
        print(f"Workspace viewer: {detail.strip() if detail else str(exc)}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Workspace viewer: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
