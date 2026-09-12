"""Real mouse/PTY exercise against disposable demo shells, never real agents."""

from __future__ import annotations

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
import tempfile
import termios
import time
from contextlib import closing, suppress
from pathlib import Path

from tmux_workspaces.application import socket_path
from tmux_workspaces.attachments import GROUPED_PREFIX
from tmux_workspaces.model import Model
from tmux_workspaces.tmux import Tmux, clean_env


class Client:
    def __init__(
        self, arguments: list[str], cols=160, rows=38, terminal_env=None, launcher=None, cwd=None
    ):
        self.master = None
        self.process = None
        self.output = b""
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            env = clean_env() | {
                "TERM": "xterm-256color",
                "LANG": "en_US.UTF-8",
                "SHELL": "/bin/sh",
            }
            env.update(terminal_env or {})
            self.process = subprocess.Popen(
                [
                    *(
                        launcher
                        or [sys.executable, str(Path(__file__).resolve().parents[2] / "run")]
                    ),
                    *arguments,
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                cwd=cwd,
                start_new_session=True,
            )
            self.master = master
        except BaseException:
            self.master = master
            self.close()
            raise
        finally:
            os.close(slave)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is None:
                raise
            exc.add_note(f"PTY cleanup also failed: {cleanup_error!r}")

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

    def click(self, x, y, *, count=1):
        # Queue a complete gesture before draining output. Host scheduling must
        # not stretch a synthetic double-click past the application's deadline.
        gesture = f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m"
        os.write(self.master, (gesture * count).encode())
        self.pump(0.3)

    def type(self, text):
        os.write(self.master, text.encode())
        self.pump(0.25)

    def resize(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.killpg(self.process.pid, signal.SIGWINCH)
        self.pump(1)

    def _signal(self, sig):
        # Popen creates a new session; this group contains only fixture clients,
        # never the separately daemonized tmux servers or their owned shells.
        with suppress(ProcessLookupError):
            os.killpg(self.process.pid, sig)

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self._signal(signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._signal(signal.SIGKILL)
                    self.process.wait(timeout=5)
                except BaseException:
                    self._signal(signal.SIGKILL)
                    self.process.wait(timeout=5)
                    raise
        finally:
            if self.master is not None:
                os.close(self.master)
                self.master = None

    def close_terminal(self):
        if self.master is not None:
            os.close(self.master)
            self.master = None
        try:
            self.process.wait(timeout=10)
        except BaseException:
            self.close()
            raise


class OuterClient(Client):
    def __init__(self, socket: str):
        super().__init__([], launcher=["tmux", "-S", socket, "attach-session", "-t", "=host:"])

    def prefix_key(self, tmux: Tmux, key: str) -> None:
        # Distinct gestures avoid tmux classifying the prefix plus key as paste.
        self.type("\x02")
        wait(
            self,
            lambda: tmux.run("list-clients", "-F", "#{client_prefix}") == "1",
            "outer tmux did not enter prefix mode",
        )
        self.type(key)


_OWNED_LIBRARIES: dict[Path, bool] = {}


def _stop_server(server: Tmux) -> None:
    # The explicit owned server is the only authority for shell PIDs. Wait before
    # removing their HOME: a login shell may still be writing history after tmux exits.
    pids = []
    try:
        pids = [
            int(pid)
            for pid in server.run("list-panes", "-a", "-F", "#{pane_pid}", check=False).splitlines()
        ]
    finally:
        server.run("kill-server", check=False)
    deadline = time.monotonic() + 5
    while pids:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            # Container init may defer reaping exited children. Zombies have no
            # descriptors and cannot write history, so they are already finished.
            status = Path(f"/proc/{pid}/stat")
            try:
                if status.read_text().rsplit(") ", 1)[-1].startswith("Z "):
                    continue
            except ProcessLookupError:
                # Linux may open the proc entry, then lose its process before
                # read completes (ESRCH). The owned shell has finished exiting.
                continue
            except FileNotFoundError:
                pass
            alive.append(pid)
        if not alive:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Fixture shell processes did not exit: {alive}")
        pids = alive
        time.sleep(0.05)


def cleanup_library(library: Path) -> None:
    """Clean only a library freshly allocated by a live FixtureResources owner.

    Runtime manifests are observations, not cleanup authority. A broken or hostile
    manifest must never redirect teardown at an existing user's tmux server.
    """
    library = library.resolve()
    if library not in _OWNED_LIBRARIES:
        raise ValueError("Library was not allocated by this test fixture")
    shell_socket = Path(socket_path(library, "terminals"))
    view_prefix = Path(socket_path(library, "view-"))
    failures = []
    sockets = sorted(view_prefix.parent.glob(view_prefix.name.removesuffix(".sock") + "*.sock"))
    if _OWNED_LIBRARIES[library]:
        demo_prefix = Path(socket_path(library, "demo-"))
        sockets.extend(
            sorted(demo_prefix.parent.glob(demo_prefix.name.removesuffix(".sock") + "*.sock"))
        )
    sockets.append(shell_socket)
    for socket in sockets:
        if socket.is_symlink():
            continue
        try:
            _stop_server(Tmux(str(socket)))
        except BaseException as exc:
            failures.append(exc)
    try:
        Path(str(shell_socket) + ".viewer-lock").unlink(missing_ok=True)
    except BaseException as exc:
        failures.append(exc)
    if failures:
        raise BaseExceptionGroup("Library cleanup failed", failures)


class FixtureResources:
    """Own fresh scratch resources, closing everything even after partial failure.

    Constructing this owner always allocates a fresh directory. Existing paths
    cannot be adopted as libraries or servers. Files remain until clients and
    private servers have had their cleanup attempts.
    """

    def __init__(self, *, parent: Path | None = None, prefix="tw-fixture-"):
        self._temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=parent or "/tmp")
        self.root = Path(self._temporary.name).resolve()
        self._libraries: list[Path] = []
        self._servers: list[Tmux] = []
        self._clients: list[Client] = []
        self._callbacks = []
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is None:
                raise
            exc.add_note(f"Fixture cleanup also failed: {cleanup_error!r}")

    def _new_path(self, name: str) -> Path:
        if self._closed:
            raise ValueError("Fixture already closed")
        if not name or Path(name).name != name or name in (".", ".."):
            raise ValueError("Fixture names must be single path components")
        path = self.root / name
        if path.exists() or path.is_symlink():
            raise ValueError("Fixture resource already exists")
        return path

    def library(self, name="library", *, demo=False) -> Path:
        path = self._new_path(name)
        path.mkdir(mode=0o700)
        self._libraries.append(path)
        _OWNED_LIBRARIES[path] = demo
        return path

    def server(self, name="source") -> Tmux:
        self._new_path(name)  # Validate the requested name before adding the socket suffix.
        # A parent supplied by tempfile may exceed macOS Unix socket limits.
        # Use the application's private short namespace, keyed by our fresh root.
        socket = str(Path(socket_path(self.root, "fixture-" + name)).resolve())
        if Path(socket).exists() or Path(socket).is_symlink():
            raise ValueError("Fixture server socket already exists")
        if any(server.socket == socket for server in self._servers):
            raise ValueError("Fixture server already registered")
        server = Tmux(socket)
        self._servers.append(server)
        return server

    def own_client(self, client: Client) -> Client:
        if self._closed:
            client.close()
            raise ValueError("Fixture already closed")
        if client not in self._clients:
            self._clients.append(client)
        return client

    def client(self, arguments: list[str], **kwargs) -> Client:
        if self._closed:
            raise ValueError("Fixture already closed")
        kwargs["terminal_env"] = {"HOME": str(self.root)} | (kwargs.get("terminal_env") or {})
        return self.own_client(Client(arguments, **kwargs))

    def own_cleanup(self, callback):
        if self._closed:
            raise ValueError("Fixture already closed")
        self._callbacks.append(callback)

    def close(self):
        if self._closed:
            return
        self._closed = True
        failures = []
        for cleanup in (
            *[client.close for client in reversed(self._clients)],
            *reversed(self._callbacks),
            *[lambda path=path: cleanup_library(path) for path in reversed(self._libraries)],
            *[lambda server=server: _stop_server(server) for server in reversed(self._servers)],
            self._temporary.cleanup,
        ):
            try:
                cleanup()
            except BaseException as exc:
                failures.append(exc)
        for library in self._libraries:
            _OWNED_LIBRARIES.pop(library, None)
        if failures:
            raise BaseExceptionGroup("Fixture cleanup failed", failures)


def right_click(client: Client, viewer: Tmux, row: int, column: int = 3) -> None:
    top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
    os.write(client.master, f"\x1b[<2;{column};{row + top + 1}M".encode())
    client.pump(0.08)
    os.write(client.master, f"\x1b[<2;{column};{row + top + 1}m".encode())
    client.pump(0.3)


OUTLINE = str.maketrans(dict.fromkeys("╭╮╰╯│─", " "))


def user_sessions(server: Tmux, fields: str = "#{session_name}") -> list[str]:
    """The sessions people run on a server: every session that is not one of the
    viewer's own grouped attach sessions."""
    listed = server.run("list-sessions", "-F", "#{session_name}\t" + fields, check=False)
    kept = []
    for line in listed.splitlines():
        name, _tab, rest = line.partition("\t")
        if not name.startswith(GROUPED_PREFIX):
            kept.append(rest)
    return kept


def grouped_sessions(server: Tmux) -> list[str]:
    """The viewer's grouped attach sessions currently on a server."""
    return [
        line
        for line in server.run("list-sessions", "-F", "#{session_name}", check=False).splitlines()
        if line.startswith(GROUPED_PREFIX)
    ]


def content_panes(viewer: Tmux) -> list[str]:
    """The sidebar and content pane ids: every pane that is not a gutter."""
    return [
        line.split()[0]
        for line in viewer.run("list-panes", "-F", "#{pane_id} #{?@viewer_gutter,1,0}").splitlines()
        if line.endswith(" 0")
    ]


def sidebar(viewer: Tmux) -> str:
    """The sidebar's text with its outline blanked, so columns stay in place
    while lines strip and compare as they did before the panel had a frame."""
    return viewer.run("capture-pane", "-p", "-t", "%0").translate(OUTLINE)


def tab_row(viewer: Tmux, name: str) -> int:
    return next(
        i
        for i, line in enumerate(sidebar(viewer).splitlines())
        if name in line and line.lstrip("▶ ").split(" ", 1)[0].isdigit()
    )


def wait(client, predicate, description, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        if client is None:
            time.sleep(0.05)
        else:
            client.pump(0.1)
    output = b"" if client is None else client.output[-3000:]
    raise AssertionError(description + "\n" + output.decode(errors="replace"))


def saved(directory: Path) -> Model:
    with closing(sqlite3.connect(directory / "layouts.db")) as db:
        return Model(
            json.loads(db.execute("SELECT value FROM terminal_layout WHERE id = 1").fetchone()[0])
        )


def open_terminal(client, viewer, library: Path) -> None:
    """Pick the shell in a new tab's chooser and wait until that shell exists.

    A new tab opens empty. Scenarios that go on to type into the tab choose its
    terminal here, waiting for the chooser first so the Enter cannot be swallowed
    by a pane that is still being spawned.
    """
    from tmux_workspaces.chooser import TERMINAL
    from tmux_workspaces.shells import Shells

    def chooser_pane():
        focus = saved(library).tab["focus"]
        for line in viewer.run(
            "list-panes", "-F", "#{pane_id}|#{@viewer_leaf_id}", check=False
        ).splitlines():
            pane, leaf = line.split("|", 1)
            if leaf == focus:
                return pane
        return None

    def shown() -> bool:
        pane = chooser_pane()
        return bool(pane) and TERMINAL in viewer.run("capture-pane", "-p", "-t", pane, check=False)

    wait(client, shown, "the new tab did not show its chooser")
    client.type("\r")
    wait(client, lambda: not saved(library).pane.get("empty"), "Enter did not open a terminal")
    name = Shells.name(saved(library).pane)
    shells = Tmux(socket_path(library, "terminals"))
    wait(
        client,
        lambda: name in shells.run("list-sessions", "-F", "#{session_name}", check=False),
        "the chosen terminal was not created",
    )
    # Typing is only safe once the pane's client is attached to that shell;
    # text sent before then can be lost while the attachment starts.
    wait(client, lambda: shell_attached(shells, name), "the chosen terminal did not attach")


def session_in_use(server: Tmux, target: str) -> bool:
    """Whether a client is attached to the session or, for an external session
    the viewer joins through a grouped session of its own, to its group."""
    counts = server.run(
        "display-message",
        "-p",
        "-t",
        target,
        "#{session_attached} #{session_group_attached}",
        check=False,
    ).split()
    return sum(int(count) for count in counts if count.isdigit()) > 0


def shell_attached(shells: Tmux, name: str) -> bool:
    attached = shells.run(
        "display-message", "-p", "-t", "=" + name + ":", "#{session_attached}", check=False
    )
    return attached.isdigit() and int(attached) > 0


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


# Pane actions moved from panel buttons into the tab menu; scenarios keep
# naming the old buttons and are routed through the menu.
MENU_ROUTES = {
    "Split →": "Split right",
    "Split ↓": "Split below",
    "Focus": "Focus one pane",
    "Layout": "Restore layout",
    "Next →": "Next pane",
    "Attach session…": "Attach session",
}
# Leaving is labelled for what it does: shells and sessions keep running.
RENAMED = {"Exit": "Detach"}
# Infrequent controls sit behind Configure…; scenarios keep naming them.
CONFIGURE = {
    "Edit theme…",
    "Edit shortcuts…",
    "View shortcuts…",
    "Detach",
    "Refresh viewer…",
    "[x] Show agent status",
    "[ ] Show agent status",
}


def click_button(client, viewer, text):
    """Click the control labelled `text`, opening the menu it lives in first.

    A click can miss on a loaded host: the sidebar redraws underneath it and
    the row measured a moment ago holds a different button, or the press and
    release straddle a repaint. A menu that should have opened is therefore
    checked for the label it must show, and the opening click repeated a few
    times before the scenario gives up; every later step verifies its own
    effect already.
    """
    text = RENAMED.get(text, text)
    opener = None
    if text in CONFIGURE:
        opener = "Configure…"
    if text == "+ Tab":
        # The plus sits at the right end of the tabs label row.
        text = "+"
    if text in MENU_ROUTES:
        opener, text = "Tab actions…", MENU_ROUTES[text]
    if text == "Tab actions…":
        # The selected tab's menu sits behind the ellipsis at its row's end.
        text = "⋯"
    if opener == "Tab actions…":
        opener = "⋯"
    if not opener:
        _click(client, viewer, text)
        return
    for attempt in range(4):
        if attempt:
            print(f"RETRY: opening {opener} to reach {text} (attempt {attempt + 1}/4)", flush=True)
            # A missed click may have opened something else, or nothing;
            # Escape leaves a menu and is harmless on the plain sidebar.
            client.type("\x1b")
            client.pump(0.3)
        _click(client, viewer, opener)
        if not _appears(client, viewer, text, timeout=4.0):
            assert attempt < 3, f"{opener} never opened a menu showing {text}"
            continue
        try:
            _click(client, viewer, text, timeout=4.0)
            return
        except AssertionError as error:
            # The menu showed the row and then went away before it could be
            # clicked. Say where the keyboard focus sits, then open it again.
            panes = viewer.run(
                "list-panes", "-F", "#{pane_id} active=#{pane_active} gutter=#{@viewer_gutter}"
            )
            assert attempt < 3, f"{text} vanished after {opener} opened; panes:\n{panes}\n{error}"


def _appears(client, viewer, text, timeout: float) -> bool:
    """Whether `text` is drawn in the sidebar within `timeout`, without failing."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in viewer.run("capture-pane", "-p", "-t", "%0"):
            return True
        client.pump(0.1)
    return False


def _click(client, viewer, text, timeout: float = 10):
    def sidebar():
        return viewer.run("capture-pane", "-p", "-t", "%0")

    seen: dict = {}

    def settled() -> bool:
        """The label is drawn at the same terminal position as a moment ago.

        Waiting only for the text to appear can measure a half-drawn sidebar,
        where the rows sit at different offsets than the finished frame. The row
        computed from that capture then clicks whatever the finished frame puts
        there instead -- one row up, in practice, which is a different button.
        Require its row, column and pane offset to repeat before using them.
        """
        lines = sidebar().splitlines()
        row = next((index for index, line in enumerate(lines) if text in line), None)
        top = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_top}"))
        position = None if row is None else (row, lines[row].index(text), top)
        previous = seen.get("position")
        seen.update(position=position, lines=lines)
        return position is not None and position == previous

    # A one-glyph control sits at the right end of its hit area, so it is
    # clicked on the glyph itself; a word is clicked one cell in.
    offset = 1 if len(text) == 1 else 2
    for attempt in range(3):
        if attempt:
            print(f"RETRY: menu click {text} (attempt {attempt + 1}/3)", flush=True)
        # Resolve again after a failed attempt: a resize or redraw may have
        # moved the control since the preceding click.
        seen.clear()
        wait(client, settled, "missing button: " + text, timeout=timeout)
        row, column, top = seen["position"]
        before = "\n".join(seen["lines"])
        client.click(column + offset, row + top + 1)
        # A row of an open menu always changes the panel when clicked: it
        # opens another menu, closes this one or shows a message. A click that
        # left the menu exactly as it was is one the loaded host dropped, so
        # it is sent again; buttons on the plain panel are never repeated,
        # since a second click on a name would begin renaming it.
        if "‹ Back" not in before:
            return
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            try:
                changed = sidebar() != before
            except RuntimeError:
                # The row ended the viewer (Detach, a refresh): that is the change.
                return
            if changed:
                return
            client.pump(0.1)
    raise AssertionError(f"menu click {text} had no visible effect after three attempts")
