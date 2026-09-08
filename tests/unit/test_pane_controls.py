import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces.controls import Actions, send_action, valid_action
from tmux_workspaces.display import Display
from tmux_workspaces.model import identity, leaf, leaves, split
from tmux_workspaces.tmux import Tmux, clean_env


class PaneControlTests(unittest.TestCase):
    def test_plain_borders_keep_attachment_shortcut_on_all_supported_tmux(self):
        display = Display("/viewer", "/source", "%0", "/shells", "/actions")
        display.tmux = Mock()
        display.setup()
        display.tmux.run.assert_any_call("set-window-option", "-g", "pane-border-status", "off")
        self.assertFalse(
            any("MouseDown1Control0" in call.args for call in display.tmux.run.call_args_list)
        )
        self.assertTrue(
            any(call.args[:2] == ("bind-key", "a") for call in display.tmux.run.call_args_list)
        )

    def test_targeted_actions_require_complete_stable_identifiers(self):
        self.assertTrue(valid_action("attach-pane:0123456789ab:abcdef012345"))
        for action in (
            "attach-pane",
            "attach-pane:0123456789ab",
            "attach-pane:0123456789ab:abcdef012345:extra",
            "attach-pane:0123456789ab:%1",
            "attach-pane:0123456789ab:abcdef01234z",
            "attach-pane:0123456789ab:abcdef012345\n",
        ):
            with self.subTest(action=action):
                self.assertFalse(valid_action(action))
                with self.assertRaises(ValueError):
                    send_action("/unused", action)

    @unittest.skipUnless(shutil.which("tmux"), "tmux required for real pane clicks")
    def test_plain_borders_preserve_mouse_focus_and_keyboard_attachment(self):
        with tempfile.TemporaryDirectory(prefix="tw-head-", dir="/tmp") as directory:
            root = Path(directory)
            viewer = Tmux(str(root / "tmux_workspaces.application.sock"))
            shells = Tmux(str(root / "shells.sock"))
            receiver = Actions(str(root / "actions.sock"))
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
            client = None
            output = bytearray()

            def pump(seconds=0.08):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], 0.01)[0]:
                        output.extend(os.read(master, 65536))

            def click(x, y):
                os.write(master, f"\x1b[<0;{x + 1};{y + 1}M".encode())
                pump()
                os.write(master, f"\x1b[<0;{x + 1};{y + 1}m".encode())

            def received(expected):
                result = []
                deadline = time.monotonic() + 5
                while len(result) < len(expected) and time.monotonic() < deadline:
                    result.extend(receiver.pending())
                    pump()
                self.assertEqual(result, expected, output[-3000:].decode(errors="replace"))

            try:
                viewer.run("-f", "/dev/null", "new-session", "-d", "-s", "viewer", "/bin/sh")
                sidebar = viewer.run("display-message", "-p", "#{pane_id}")
                client = subprocess.Popen(
                    ["tmux", "-S", viewer.socket, "attach-session", "-t", "=viewer:"],
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                    env=clean_env() | {"TERM": "xterm-256color"},
                )
                deadline = time.monotonic() + 5
                while not viewer.run("list-clients"):
                    self.assertLess(time.monotonic(), deadline)
                    pump()
                display = Display(
                    viewer.socket, str(root / "absent.sock"), sidebar, shells.socket, receiver.path
                )
                display.setup()
                tree = leaf(directory)
                first = tree["id"]
                right = split(tree, first, "right", directory)
                split(tree, first, "below", directory)
                split(tree, right, "below", directory)
                tab = {"id": identity(), "tree": tree, "focus": first}
                with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
                    display.render(tab, False)
                pump(0.3)
                self.assertNotIn(b"Attach session", output)
                self.assertEqual(viewer.run("show-options", "-gwv", "pane-border-status"), "off")
                for item in leaves(tree):
                    with self.subTest(leaf=item["id"]):
                        pane = display.panes[item["id"]]
                        viewer.run("select-pane", "-t", sidebar)
                        left, top = map(
                            int,
                            viewer.run(
                                "display-message", "-p", "-t", pane, "#{pane_left} #{pane_top}"
                            ).split(),
                        )
                        self.assertEqual(
                            viewer.run("show-options", "-pv", "-t", pane, "@viewer_leaf_id"),
                            item["id"],
                        )
                        click(left + 2, top + 1)
                        pump(0.1)
                        self.assertEqual(display.focused_leaf(), item["id"])
                        self.assertEqual(list(receiver.pending()), [])
                        os.write(master, b"\x07a")
                        received(["attach"])
                # A narrow terminal focuses one pane without restoring a title row.
                leaves(tree)[0]["agent"] = None
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 44, 0, 0))
                os.kill(client.pid, signal.SIGWINCH)
                deadline = time.monotonic() + 5
                while display.size()[0] != 44:
                    self.assertLess(time.monotonic(), deadline)
                    pump()
                with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
                    display.render(tab, False)
                pump(0.3)
                self.assertNotIn(b"[ Attach ]", output)
                pane = display.panes[first]
                left, top = map(
                    int,
                    viewer.run(
                        "display-message", "-p", "-t", pane, "#{pane_left} #{pane_top}"
                    ).split(),
                )
                self.assertEqual(top, 0)
                click(left + 2, top + 1)
                pump(0.1)
                self.assertEqual(display.focused_leaf(), first)
                os.write(master, b"\x07a")
                received(["attach"])
            finally:
                viewer.run("kill-server", check=False)
                shells.run("kill-server", check=False)
                receiver.close()
                os.close(slave)
                os.close(master)
                if client is not None:
                    client.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
