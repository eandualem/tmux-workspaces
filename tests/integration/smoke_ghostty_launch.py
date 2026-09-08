"""Exercise Ghostty's generated shell command on a real isolated PTY.

macOS executes Ghostty's bash wrapper; Linux checks portable shell quoting and
lifecycle. Neither path runs the native GUI or /usr/bin/login.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

from tests.integration.support import Client, click_button, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import direct_sequence, send_action
from tmux_workspaces.ghostty_launcher import launch_command
from tmux_workspaces.model import leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux, clean_env


class GhosttyShellClient(Client):
    def __init__(self, library: Path, source_socket: str):
        command = launch_command(
            ["--data-dir", str(library), "--source-socket", source_socket],
            cwd=library.parent,
        )
        shell_command = next(value for value in command if value.startswith("--command="))
        assert shell_command.startswith("--command=shell:")
        value = shell_command.removeprefix("--command=shell:")
        env = clean_env() | {"TERM": "xterm-256color", "LANG": "en_US.UTF-8", "SHELL": "/bin/sh"}
        terminfo = Path("/Applications/Ghostty.app/Contents/Resources/terminfo")
        if terminfo.is_dir():
            env.update(TERM="xterm-ghostty", TERMINFO=str(terminfo))
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 160, 0, 0))
        try:
            # Ghostty uses login argv[0] only on macOS. Imposing that wrapper on
            # Linux can prevent CPython from resolving its own executable.
            wrapper = (
                ["/bin/bash", "--noprofile", "--norc", "-c", "exec -l " + value]
                if sys.platform == "darwin"
                else ["/bin/sh", "-c", "exec " + value]
            )
            # The Linux exec keeps PID tracking simple in this test harness.
            self.process = subprocess.Popen(
                wrapper,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=library.parent,
                env=env,
                start_new_session=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.output = b""


def exercise(directory: Path) -> None:
    # Spaces and a literal apostrophe also exercise the actual launch quoting.
    library = directory / "launch library 'quoted'"
    source_socket = socket_path(directory, "absent-source")
    shells = Tmux(socket_path(library, "terminals"))
    client = GhosttyShellClient(library, source_socket)
    try:

        def initialized():
            wait(client, lambda: client.manifest(library), "Ghostty shell wrapper did not launch")
            viewer = Tmux(json.loads(client.manifest(library).read_text())["viewer_socket"])
            wait(
                client,
                lambda: "Layouts saved" in viewer.run("capture-pane", "-p", "-t", "%0"),
                "launched sidebar did not initialize",
            )
            return viewer

        def content_ready(viewer):
            name = Shells.name(saved(library).pane)
            return (
                any(
                    line.startswith("1 ") and name in line
                    for line in viewer.run(
                        "list-panes", "-F", "#{pane_active} #{pane_start_command}"
                    ).splitlines()
                )
                and int(
                    shells.run(
                        "display-message", "-p", "-t", "=" + name + ":", "#{session_attached}"
                    )
                    or 0
                )
                > 0
            )

        def stable(tab_count, seconds=1.5):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                assert client.process.poll() is None, "launched viewer exited unexpectedly"
                assert len(list((library / "windows").glob("*/runtime.json"))) == 1, (
                    "one launch unexpectedly created additional viewers"
                )
                assert len(saved(library).space["tabs"]) == tab_count, (
                    "one launch/key unexpectedly created additional tabs"
                )
                client.pump(0.1)

        viewer = initialized()
        initial = saved(library)
        assert len(initial.space["tabs"]) == 1
        terminal = "=" + Shells.name(initial.pane) + ":"
        wait(client, lambda: content_ready(viewer), "initial shell did not attach")
        shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        stable(1)
        client.type("export GHOSTTY_LAUNCH_STATE=retained; cd /tmp\r")
        client.type(direct_sequence("new-tab"))
        wait(
            client,
            lambda: len(saved(library).space["tabs"]) == 2 and content_ready(viewer),
            "one direct new-tab action failed after the Ghostty shell wrapper",
        )
        stable(2)
        client.type(direct_sequence("split-right"))
        wait(
            client,
            lambda: len(leaves(saved(library).tab["tree"])) == 2 and content_ready(viewer),
            "direct split failed after the Ghostty shell wrapper",
        )
        client.type(direct_sequence("rename-tab") + "Launch checked\r")
        wait(
            client,
            lambda: saved(library).tab["name"] == "Launch checked" and content_ready(viewer),
            "direct rename failed after the Ghostty shell wrapper",
        )
        stable(2)
        click_button(client, viewer, "Exit")
        wait(client, lambda: client.process.poll() is not None, "launched viewer did not exit")
        assert client.process.returncode == 0, (client.process.returncode, client.output[-700:])
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        client.close()

        client = GhosttyShellClient(library, source_socket)
        viewer = initialized()
        model = saved(library)
        assert model.space["id"] == initial.space["id"]
        assert model.space["tabs"][0]["id"] == initial.tab["id"]
        assert model.space["tabs"][1]["name"] == "Launch checked"
        assert len(leaves(model.space["tabs"][1]["tree"])) == 2
        stable(2)
        client.type(direct_sequence("select-tab-1"))
        wait(
            client,
            lambda: saved(library).tab["id"] == initial.tab["id"] and content_ready(viewer),
            "reopened viewer did not select the original shell",
        )
        client.type('printf \'LAUNCH_RETAINED:%s:%s\\n\' "$GHOSTTY_LAUNCH_STATE" "$PWD"\r')
        wait(
            client,
            lambda: (
                "LAUNCH_RETAINED:retained:/tmp" in shells.run("capture-pane", "-p", "-t", terminal)
            ),
            "Ghostty shell wrapper reopen lost shell environment/cwd",
        )
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        click_button(client, viewer, "Exit")
        wait(client, lambda: client.process.poll() is not None, "reopened viewer did not exit")
        assert client.process.returncode == 0, (client.process.returncode, client.output[-700:])
        assert not Path(source_socket).exists(), "test created an external source server"
        assert b"exec: exec: not found" not in client.output
        client.close()

        # Freeze only this fixture's attached tmux client while the sidebar exits.
        # A detach request is asynchronous: killing the server before the client
        # can acknowledge it races a normal close into a failure exit status.
        client = GhosttyShellClient(library, source_socket)
        viewer = initialized()
        wait(client, lambda: content_ready(viewer), "paused-client fixture did not attach")
        # Keep the sidebar alive until its own finally block finishes; otherwise
        # destroying its session can SIGHUP it before the old premature kill.
        viewer.run("set-option", "-g", "destroy-unattached", "off")
        attached = viewer.run("list-clients", "-F", "#{client_pid}|#{session_name}").splitlines()
        assert len(attached) == 1, "fixture must own exactly one attached client"
        attached_pid, attached_session = attached[0].split("|", 1)
        attached_pid = int(attached_pid)
        assert attached_session == "viewer"
        assert attached_pid > 1 and attached_pid not in {os.getpid(), client.process.pid}
        server_pid = viewer.run("display-message", "-p", "#{pid}")
        sidebar_pid = int(viewer.run("display-message", "-p", "-t", "%0", "#{pane_pid}"))

        def sidebar_exited() -> bool:
            try:
                os.kill(sidebar_pid, 0)
            except ProcessLookupError:
                return True
            return False

        instance = client.manifest(library).parent.name
        action_socket = socket_path(library, "actions-" + instance)
        try:
            os.kill(attached_pid, signal.SIGSTOP)
            send_action(action_socket, "quit", wait=True)
            # Pane removal can precede the sidebar's final cleanup commands.
            # Wait for the actual process to finish before checking that the
            # server remains alive for this client's pending detach handshake.
            wait(client, sidebar_exited, "sidebar did not finish its detach request", timeout=5)
            assert viewer.run("display-message", "-p", "#{pid}") == server_pid
            # list-clients excludes clients whose session was destroyed, even
            # though their exit handshake is still pending. Check our owned PID.
            os.kill(attached_pid, 0)
            assert client.process.poll() is None, "launcher finished before detach acknowledgement"
            assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        finally:
            # Always resume this owned client, including when an assertion fails.
            with contextlib.suppress(ProcessLookupError):
                os.kill(attached_pid, signal.SIGCONT)
        wait(client, lambda: client.process.poll() is not None, "resumed client did not exit")
        assert client.process.returncode == 0, (client.process.returncode, client.output[-700:])
        # tmux can leave a stale socket pathname after exiting. A successful
        # server query, rather than pathname existence, determines liveness.
        wait(
            client,
            lambda: viewer.run("display-message", "-p", "#{pid}", check=False) == "",
            "detached viewer server remained",
        )
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        client.close()

        # Intentional loss of this disposable viewer server must still report
        # failure. A normal-exit fix must not turn every tmux failure into success.
        client = GhosttyShellClient(library, source_socket)
        viewer = initialized()
        wait(client, lambda: content_ready(viewer), "failure fixture did not attach its shell")
        viewer.run("kill-server")
        wait(client, lambda: client.process.poll() is not None, "server loss did not end viewer")
        assert client.process.returncode != 0, "unexpected viewer server loss reported success"
        assert shell_pid == shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
        print(
            "PASS: generated Ghostty command through macOS bash exec wrapper on a real PTY; "
            "one viewer/tab, direct new-tab/split/rename, exit/reopen retains library and shell "
            "PID/cwd/environment; normal exit and paused-client detach succeed, server loss fails "
            "(no Ghostty GUI or login session)",
            flush=True,
        )
    finally:
        client.close()
        for path in (library / "windows").glob("*/runtime.json"):
            with contextlib.suppress(FileNotFoundError, ValueError):
                Tmux(json.loads(path.read_text())["viewer_socket"]).run("kill-server", check=False)
        shells.run("kill-server", check=False)
        Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="tw-ghostty-launch-", dir="/tmp") as directory:
        exercise(Path(directory))
