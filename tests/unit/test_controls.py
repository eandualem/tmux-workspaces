import json
import os
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.integration.support import Client, saved, wait
from tmux_workspaces.application import socket_path
from tmux_workspaces.controls import (
    DIRECT_SHORTCUTS,
    Actions,
    direct_sequence,
    mouse_action,
    valid_action,
)
from tmux_workspaces.display import Display
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux, clean_env


class ControlTests(unittest.TestCase):
    def test_mouse_actions_reject_unbounded_or_executable_coordinates(self):
        self.assertEqual(mouse_action("mouse:left:0:65535"), (0, 65535))
        for action in (
            "mouse:left:-1:0",
            "mouse:left:0:65536",
            "mouse:left:100000:0",
            "mouse:left:1:2;quit",
            "mouse:left:$(id):2",
            "mouse:left:1:2\n",
            "mouse:right:1:2",
            "mouse:left:١:2",
        ):
            with self.subTest(action=action):
                self.assertIsNone(mouse_action(action))
                self.assertFalse(valid_action(action))

    @unittest.skipUnless(shutil.which("tmux"), "tmux required for real key ordering test")
    def test_burst_of_direct_keys_reaches_action_receiver_in_input_order(self):
        with tempfile.TemporaryDirectory(prefix="tw-keys-", dir="/tmp") as directory:
            root = Path(directory)
            tmux = Tmux(str(root / "viewer.sock"))
            receiver = Actions(str(root / "actions.sock"))
            client = None
            master = slave = None
            try:
                tmux.run("-f", "/dev/null", "new-session", "-d", "-s", "viewer", "/bin/sh")
                sidebar = tmux.run("display-message", "-p", "-t", "=viewer:", "#{pane_id}")
                master, slave = pty.openpty()
                client = subprocess.Popen(
                    ["tmux", "-S", tmux.socket, "attach-session", "-t", "=viewer:"],
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    env=clean_env() | {"TERM": "xterm-256color"},
                )
                os.close(slave)
                slave = None

                def pump():
                    if select.select([master], [], [], 0.01)[0]:
                        os.read(master, 65536)

                deadline = time.monotonic() + 5
                while not tmux.run("list-clients"):
                    self.assertLess(time.monotonic(), deadline, "fixture client did not attach")
                    pump()
                # Use production binding setup and real _action subprocesses.
                # No sidebar consumes these actions: only the test receiver does.
                display = Display(
                    tmux.socket,
                    str(root / "tmux_workspaces.source.sock"),
                    sidebar,
                    str(root / "shells.sock"),
                    receiver.path,
                )
                display.setup()
                expected = list(DIRECT_SHORTCUTS)[:12]
                os.write(master, "".join(direct_sequence(action) for action in expected).encode())
                received = []
                deadline = time.monotonic() + 10
                while len(received) < len(expected) and time.monotonic() < deadline:
                    received.extend(receiver.pending())
                    pump()
                self.assertEqual(received, expected)
            finally:
                tmux.run("kill-server", check=False)
                if client is not None:
                    client.wait(timeout=5)
                receiver.close()
                if slave is not None:
                    os.close(slave)
                if master is not None:
                    os.close(master)

    @unittest.skipUnless(shutil.which("tmux"), "tmux required for immediate rename test")
    def test_immediate_text_after_shortcuts_reaches_the_requested_destination(self):
        with tempfile.TemporaryDirectory(prefix="tw-rename-", dir="/tmp") as directory:
            library = Path(directory) / "library"
            shells = Tmux(socket_path(library, "terminals"))
            client = Client(
                [
                    "--data-dir",
                    str(library),
                    "--source-socket",
                    str(Path(directory) / "absent.sock"),
                ]
            )
            try:
                wait(client, lambda: client.manifest(library), "viewer manifest missing")
                runtime = json.loads(client.manifest(library).read_text())
                viewer = Tmux(runtime["viewer_socket"])
                wait(
                    client,
                    lambda: "Layouts saved" in viewer.run("capture-pane", "-p", "-t", "%0"),
                    "viewer sidebar did not initialize",
                )
                terminal = "=" + Shells.name(saved(library).pane) + ":"
                client.type("printf 'IMMEDIATE_RENAME_READY\\n'\r")
                wait(
                    client,
                    lambda: (
                        "IMMEDIATE_RENAME_READY" in shells.run("capture-pane", "-p", "-t", terminal)
                    ),
                    "ordinary shell did not become ready",
                )
                shell_pid = shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}")
                renamed = "Immediate renamed tab"
                # One PTY write models typing without waiting for a rendered prompt.
                client.type(direct_sequence("rename-tab") + renamed + "\r")
                wait(
                    client,
                    lambda: saved(library).tab["name"] == renamed,
                    "immediate rename text did not reach the name field",
                )
                self.assertNotIn(renamed, shells.run("capture-pane", "-p", "-t", terminal))
                self.assertEqual(
                    shells.run("display-message", "-p", "-t", terminal, "#{pane_pid}"), shell_pid
                )
                client.type(
                    direct_sequence("new-tab") + "printf 'AFTER_SHORTCUT_%s\\n' NEW_SHELL\r"
                )
                wait(
                    client,
                    lambda: len(saved(library).space["tabs"]) == 2,
                    "new-tab shortcut did not create its terminal",
                )
                new_terminal = "=" + Shells.name(saved(library).pane) + ":"
                self.assertNotEqual(new_terminal, terminal)
                wait(
                    client,
                    lambda: (
                        "AFTER_SHORTCUT_NEW_SHELL"
                        in shells.run("capture-pane", "-p", "-t", new_terminal)
                    ),
                    "immediate command did not execute in the new ordinary shell",
                )
                self.assertNotIn(
                    "AFTER_SHORTCUT_NEW_SHELL", shells.run("capture-pane", "-p", "-t", terminal)
                )
            finally:
                client.close()
                shells.run("kill-server", check=False)
                Path(shells.socket + ".viewer-lock").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
