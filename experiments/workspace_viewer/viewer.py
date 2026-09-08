#!/usr/bin/env python3
"""A workspace layer for tmux. Named purposes, tabs and saved split arrangements."""

from __future__ import annotations

import argparse
import contextlib
import curses
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from controls import Actions, send_action
from model import Store
from sidebar import Sidebar
from source import Source
from targets import session_target
from terminal import Display, Tmux, clean_env, script_command


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
        "mode", nargs="?", choices=["_sidebar", "_leaf", "_action"], help=argparse.SUPPRESS
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


def attachment_hosts_viewer(args, target: str) -> bool:
    if not args.host_socket or not args.host_pane:
        return False
    if os.path.realpath(args.host_socket) != os.path.realpath(args.source_socket):
        return False
    tmux = Tmux(args.source_socket)
    # Linked windows and grouped sessions can share a pane across session IDs.
    # Pane IDs are server-wide, so inspect every window in the target session.
    panes = tmux.run("list-panes", "-s", "-t", target, "-F", "#{pane_id}", check=False)
    return args.host_pane in panes.splitlines()


def leaf_main(args) -> int:
    name = args.agent or args.terminal
    if not name:
        print(
            "\033[2J\033[HCreate a terminal with the top +, or Ctrl-g then t.",
            flush=True,
        )
        while True:
            time.sleep(60)
    target = session_target(name)
    command = ["tmux", "-S", args.source_socket, "attach-session", "-t", target]
    notice = ""
    while True:
        exists = (
            subprocess.run(
                ["tmux", "-S", args.source_socket, "has-session", "-t", target],
                env=clean_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode
            == 0
        )
        if exists and attachment_hosts_viewer(args, target):
            current = "host"
            detail = (
                "This session hosts the viewer.\r\n\r\n"
                "Choose another session, or use Tab… → Return pane to shell."
            )
        elif exists:
            notice = ""
            subprocess.run(command, env=clean_env(), check=False)
            time.sleep(1)
            continue
        else:
            current = "offline"
            detail = (
                "session offline.\r\n\r\n"
                "This pane is saved. It reconnects when the session returns."
            )
        if current != notice:
            print(
                "\033[2J\033[H" + name + " — " + detail,
                flush=True,
            )
            notice = current
        time.sleep(1)


def sidebar_main(args) -> int:
    # Wait for the launcher's attachment before enabling exit-unattached.
    tmux = Tmux(args.viewer_socket)
    deadline = time.monotonic() + 15
    while tmux.run("display-message", "-p", "-t", "viewer:", "#{session_attached}") == "0":
        if time.monotonic() > deadline:
            return 1
        time.sleep(0.05)
    source = Source(
        args.source_socket,
        backbone_data_dir=args.backbone_data_dir if args.backbone else None,
        url=args.url,
        demo=args.instance_dir / "demo.json" if args.demo else None,
    )
    source.refresh()
    store = Store(args.data_dir)
    actions = Actions(args.action_socket)
    clean_exit = False
    try:
        model = store.load()
        display = Display(
            args.viewer_socket,
            args.source_socket,
            os.environ["TMUX_PANE"],
            args.shell_socket,
            args.action_socket,
            args.host_socket,
            args.host_pane,
        )
        curses.wrapper(
            lambda screen: Sidebar(
                screen, model, store, source, display, actions, args.shortcut_hints
            ).run()
        )
        clean_exit = True
    except Exception:
        (args.instance_dir / "error.txt").write_text(traceback.format_exc())
        raise
    finally:
        actions.close()
        source.close()
        store.close()
        if clean_exit:
            # Detach normally so the outer tmux client returns success. Killing
            # its server first reports an error to terminal launchers like Ghostty.
            tmux.run("detach-client", "-s", "=viewer:", check=False)
        tmux.run("kill-server", check=False)
    return 0


def socket_path(data_dir: Path, suffix: str = "view") -> str:
    # macOS TMPDIR can consume almost the entire Unix socket path length limit.
    directory = Path("/tmp") / f"tmux-workspaces-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Viewer socket directory must be private and owned by you")
    digest = hashlib.sha256(str(data_dir.resolve()).encode()).hexdigest()[:16]
    return str(directory / f"{digest}-{suffix}.sock")


def start_demo(socket: str, data_dir: Path) -> None:
    tmux = Tmux(socket)
    items = []
    for index, name in enumerate(("manager", "builder", "reviewer", "tester", "researcher")):
        # These shells are owned by the demo and isolated from all Backbone sessions.
        subprocess.run(
            [
                "tmux",
                "-S",
                socket,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                name,
                "-x",
                "120",
                "-y",
                "35",
                f"printf 'Demo shell: {name}\\n'; PS1='{name}> ' /bin/sh -i",
            ],
            env=clean_env(),
            check=True,
            capture_output=True,
        )
        tmux.run("set-option", "-t", "=" + name + ":", "status", "off")
        tmux.run("set-option", "-t", "=" + name + ":", "mouse", "on")
        items.append(
            {
                "name": name,
                "state": "unknown",
                "configured": True,
                "online": True,
                "tags": ["swarm:demo-build" if index < 4 else "research"],
            }
        )
    items.append(
        {"name": "notes", "state": "offline", "configured": True, "online": False, "tags": []}
    )
    (data_dir / "demo.json").write_text(json.dumps(items))


def launch(args) -> int:
    if not args.backbone and (args.backbone_data_dir is not None or args.url is not None):
        raise ValueError("Use --backbone to enable Backbone configuration and API access")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Run the viewer in an interactive terminal")
    if not shutil.which("tmux"):
        raise ValueError("tmux is required (macOS, Linux, or WSL)")
    outer = os.environ.get("TMUX", "").rsplit(",", 2)
    if not args.host_socket and len(outer) == 3:
        args.host_socket = outer[0]
        args.host_pane = os.environ.get("TMUX_PANE")
    args.data_dir = args.data_dir.expanduser().resolve()
    if args.demo:
        args.data_dir = args.data_dir / "demo"
    if args.backbone:
        args.backbone_data_dir = (
            (
                args.backbone_data_dir
                or Path(os.environ.get("BACKBONE_DATA_DIR") or "~/.local/share/agent-backbone")
            )
            .expanduser()
            .resolve()
        )
    args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance = uuid.uuid4().hex[:12]
    args.instance_dir = args.data_dir / "windows" / instance
    args.instance_dir.mkdir(parents=True, mode=0o700)
    try:
        viewer_socket = socket_path(args.data_dir, "view-" + instance)
        shell_socket = socket_path(args.data_dir, "terminals")
        action_socket = socket_path(args.data_dir, "actions-" + instance)
        source_socket = args.source_socket
        if args.demo:
            source_socket = socket_path(args.data_dir, "demo-" + instance)
        elif not source_socket:
            source_socket = default_source_socket()
        source_socket = str(Path(source_socket).resolve())
        if source_socket in (viewer_socket, shell_socket):
            raise ValueError("Viewer, terminal and source sockets must differ")
        tmux = Tmux(viewer_socket)
        # Every terminal window owns its own server, selection and cleanup.
        # Never stop another window (or original agent sessions) on startup.
        cols, rows = shutil.get_terminal_size()
        child_args = [
            "_sidebar",
            "--data-dir",
            str(args.data_dir),
            "--source-socket",
            source_socket,
            "--viewer-socket",
            viewer_socket,
            "--instance-dir",
            str(args.instance_dir),
            "--shell-socket",
            shell_socket,
            "--action-socket",
            action_socket,
            "--shortcut-hints",
            args.shortcut_hints,
        ]
        if args.backbone:
            child_args += ["--backbone", "--backbone-data-dir", str(args.backbone_data_dir)]
        if args.backbone and args.url:
            child_args += ["--url", args.url]
        if args.demo:
            child_args += ["--demo"]
        if args.host_socket and args.host_pane:
            child_args += ["--host-socket", args.host_socket, "--host-pane", args.host_pane]
        manifest = {
            "viewer_socket": viewer_socket,
            "source_socket": source_socket,
            "shell_socket": shell_socket,
            "pid": os.getpid(),
        }
        (args.instance_dir / "runtime.json").write_text(json.dumps(manifest))
        try:
            if args.demo:
                start_demo(source_socket, args.instance_dir)
            subprocess.run(
                [
                    "tmux",
                    "-S",
                    viewer_socket,
                    "-f",
                    "/dev/null",
                    "new-session",
                    "-d",
                    "-s",
                    "viewer",
                    "-x",
                    str(cols),
                    "-y",
                    str(rows),
                    script_command(*child_args),
                ],
                env=clean_env(),
                check=True,
                capture_output=True,
            )
            result = subprocess.call(
                ["tmux", "-S", viewer_socket, "attach-session", "-t", "=viewer:"], env=clean_env()
            )
            error = args.instance_dir / "error.txt"
            if error.exists():
                raise RuntimeError(error.read_text().strip())
            return result
        finally:
            tmux.run("kill-server", check=False)
            Path(action_socket).unlink(missing_ok=True)
            if args.demo:
                Tmux(source_socket).run("kill-server", check=False)
    finally:
        shutil.rmtree(args.instance_dir, ignore_errors=True)


def main() -> int:
    args = parser().parse_args()
    try:
        if args.mode == "_action":
            send_action(args.action_socket, args.action, wait=args.wait_action)
            return 0
        if args.mode == "_leaf":
            return leaf_main(args)
        if args.mode == "_sidebar":
            return sidebar_main(args)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
        signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))
        return launch(args)
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


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        raise SystemExit(main())
