import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces.controls import Actions


class ActionCleanupTests(unittest.TestCase):
    def test_failed_bind_closes_descriptor_without_removing_existing_socket(self):
        with tempfile.TemporaryDirectory(prefix="tw-action-", dir="/tmp") as directory:
            path = str(Path(directory) / "actions.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as existing:
                existing.bind(path)
                inode = Path(path).stat().st_ino
                receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                with (
                    patch("tmux_workspaces.controls.socket.socket", return_value=receiver),
                    self.assertRaises(OSError),
                ):
                    Actions(path)
                self.assertEqual(receiver.fileno(), -1)
                self.assertEqual(Path(path).stat().st_ino, inode)
                with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b"still owned", path)
                existing.settimeout(1)
                self.assertEqual(existing.recv(32), b"still owned")

    def test_failure_after_bind_closes_descriptor_and_removes_owned_socket(self):
        for failure in (OSError("cannot set nonblocking"), KeyboardInterrupt()):
            with (
                self.subTest(failure=type(failure).__name__),
                tempfile.TemporaryDirectory(prefix="tw-action-", dir="/tmp") as directory,
            ):
                path = Path(directory) / "actions.sock"
                receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                wrapper = Mock(wraps=receiver)
                wrapper.setblocking.side_effect = failure
                with (
                    patch("tmux_workspaces.controls.socket.socket", return_value=wrapper),
                    self.assertRaises(type(failure)) as caught,
                ):
                    Actions(str(path))
                self.assertIs(caught.exception, failure)
                self.assertEqual(receiver.fileno(), -1)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
