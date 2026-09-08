#!/usr/bin/env python3
"""A workspace layer for tmux. Named purposes, tabs and saved split arrangements."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from .cli import default_source_socket, effective_keymap
from .controls import Actions
from .display import Display
from .entrypoints import script_command
from .persistence import Store
from .preflight import check_startup, load_curses
from .source import Source
from .tmux import Tmux, clean_env, shell_context


def make_source(
    socket: str,
    *,
    backbone_data_dir: Path | None = None,
    url: str | None = None,
    demo: Path | None = None,
) -> Source:
    """The sole integration selection boundary; normal startup imports no Backbone."""
    if demo is not None:
        from .adapters.demo import DemoProvider

        return Source(socket, DemoProvider(demo), persistent_socket=False)
    from .adapters.tmux import TmuxProvider

    overlays = ()
    if backbone_data_dir is not None:
        from .adapters.backbone import BackboneProvider

        overlays = (BackboneProvider(backbone_data_dir, url),)
    return Source(socket, TmuxProvider(socket), overlays)


@contextlib.contextmanager
def startup_cleanup():
    """Attempt every cleanup while retaining the original startup diagnostic."""
    with contextlib.ExitStack() as cleanup:
        try:
            yield cleanup
        except BaseException as original:
            try:
                cleanup.close()
            except BaseException as error:
                original.add_note(f"Startup cleanup also failed: {error}")
            raise


def sidebar_main(args) -> int:
    tmux = Tmux(args.viewer_socket)
    clean_exit = False
    try:
        curses = load_curses()
        from .sidebar import Sidebar

        # Wait for the launcher's attachment before enabling exit-unattached.
        deadline = time.monotonic() + 15
        while tmux.run("display-message", "-p", "-t", "viewer:", "#{session_attached}") == "0":
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Viewer terminal did not attach within 15 seconds; try ./run again."
                )
            time.sleep(0.05)
        with startup_cleanup() as cleanup:
            source = make_source(
                args.source_socket,
                backbone_data_dir=args.backbone_data_dir if args.backbone else None,
                url=args.url,
                demo=args.instance_dir / "demo.json" if args.demo else None,
            )
            cleanup.callback(source.close)
            source.refresh()
            store = cleanup.enter_context(contextlib.closing(Store(args.data_dir)))
            actions = cleanup.enter_context(contextlib.closing(Actions(args.action_socket)))
            model = store.load()
            display = Display(
                args.viewer_socket,
                args.source_socket,
                os.environ["TMUX_PANE"],
                args.shell_socket,
                args.action_socket,
                args.host_socket,
                args.host_pane,
                effective_keymap(args),
            )
            try:
                curses.wrapper(
                    lambda screen: Sidebar(
                        screen, model, store, source, display, actions, args.shortcut_hints
                    ).run()
                )
            except curses.error as error:
                raise RuntimeError(
                    f"Terminal initialization failed for TERM={os.environ.get('TERM', '')!r}. "
                    "Check the host's tmux terminfo and Python ncurses support; "
                    "then reopen with ./run in a supported terminal."
                ) from error
        clean_exit = True
    except BaseException as error:
        # Include setup failures too, so the outer launcher can report them after
        # tmux closes. Known environment failures need an actionable message.
        detail = str(error) if isinstance(error, RuntimeError) else traceback.format_exc()
        with contextlib.suppress(OSError):
            (args.instance_dir / "error.txt").write_text(detail)
        raise
    finally:
        original = sys.exception()
        try:
            if clean_exit:
                # Finish detach before exit-unattached retires this private server.
                tmux.run("detach-client", "-s", "=viewer:", check=False)
            else:
                tmux.run("kill-server", check=False)
        except BaseException as cleanup_error:
            if original is None:
                raise
            original.add_note(f"Private viewer cleanup also failed: {cleanup_error}")
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
    keymap = effective_keymap(args)
    if not args.backbone and (args.backbone_data_dir is not None or args.url is not None):
        raise ValueError("Use --backbone to enable Backbone configuration and API access")
    check_startup()
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
    # Validate before creating display/demo servers or ordinary shells. A corrupt
    # arrangement must not start a partial viewer or overwrite recovery data.
    with contextlib.closing(Store(args.data_dir)) as store:
        store.load()
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
            "--keymap-state",
            keymap.to_toml(),
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
        with startup_cleanup() as cleanup:
            if args.demo:
                cleanup.callback(Tmux(source_socket).run, "kill-server", check=False)
            cleanup.callback(Path(action_socket).unlink, missing_ok=True)
            cleanup.callback(tmux.run, "kill-server", check=False)
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
                # This new, private server carries session paths to its sidebar
                # so each viewer can initialize new shells from its own context.
                env=clean_env() | shell_context(),
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
        shutil.rmtree(args.instance_dir, ignore_errors=True)
