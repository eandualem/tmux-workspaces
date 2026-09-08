"""Fixture ownership and PTY teardown fail safely before and after process launch."""

import errno
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.integration.support import Client, FixtureResources, _stop_server, cleanup_library, wait


class ClientCleanupTests(unittest.TestCase):
    def test_popen_failure_closes_both_pty_descriptors(self):
        import pty

        master, slave = pty.openpty()
        with (
            patch("tests.integration.support.pty.openpty", return_value=(master, slave)),
            patch("tests.integration.support.subprocess.Popen", side_effect=OSError("launch")),
            self.assertRaisesRegex(OSError, "launch"),
        ):
            Client([])
        for fd in (master, slave):
            with self.assertRaises(OSError) as raised:
                os.fstat(fd)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_interrupted_setup_closes_both_pty_descriptors(self):
        import pty

        master, slave = pty.openpty()
        with (
            patch("tests.integration.support.pty.openpty", return_value=(master, slave)),
            patch("tests.integration.support.fcntl.ioctl", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            Client([])
        for fd in (master, slave):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def fake_client(self, effects):
        client = Client.__new__(Client)
        client.master = 901
        client.process = Mock(pid=902)
        client.process.poll.return_value = None
        client.process.wait.side_effect = effects
        return client

    def test_timeout_escalates_to_kill_and_closes_master(self):
        client = self.fake_client([subprocess.TimeoutExpired("fixture", 2), 0])
        with patch("os.killpg") as kill, patch("os.close") as close:
            client.close()
        self.assertEqual(kill.call_args_list, [((902, signal.SIGTERM),), ((902, signal.SIGKILL),)])
        self.assertEqual(client.process.wait.call_count, 2)
        close.assert_called_once_with(901)
        self.assertIsNone(client.master)

    def test_interruption_reaps_child_then_propagates(self):
        client = self.fake_client([KeyboardInterrupt(), 0])
        with (
            patch("os.killpg") as kill,
            patch("os.close") as close,
            self.assertRaises(KeyboardInterrupt),
        ):
            client.close()
        kill.assert_any_call(902, signal.SIGKILL)
        close.assert_called_once_with(901)
        self.assertEqual(client.process.wait.call_count, 2)

    def test_failed_reap_still_closes_master(self):
        client = self.fake_client(
            [subprocess.TimeoutExpired("fixture", 2), subprocess.TimeoutExpired("fixture", 5)]
        )
        with (
            patch("os.killpg"),
            patch("os.close") as close,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            client.close()
        close.assert_called_once_with(901)

    def test_terminal_hangup_timeout_still_terminates_client(self):
        client = self.fake_client([subprocess.TimeoutExpired("fixture", 10), 0])
        with (
            patch("os.killpg") as kill,
            patch("os.close") as close,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            client.close_terminal()
        kill.assert_called_once_with(902, signal.SIGTERM)
        close.assert_called_once_with(901)

    def test_real_pty_context_reaps_process_after_body_failure(self):
        with (
            self.assertRaisesRegex(RuntimeError, "body"),
            Client([], launcher=[sys.executable, "-c", "import time; time.sleep(60)"]) as client,
        ):
            self.assertIsNone(client.process.poll())
            raise RuntimeError("body")
        self.assertIsNotNone(client.process.poll())
        self.assertIsNone(client.master)
        client.close()


class FixtureOwnershipTests(unittest.TestCase):
    def test_arbitrary_library_is_rejected_without_any_tmux_command(self):
        with tempfile.TemporaryDirectory() as path, patch("tests.integration.support.Tmux") as tmux:
            with self.assertRaisesRegex(ValueError, "not allocated"):
                cleanup_library(Path(path))
            tmux.assert_not_called()

    def test_names_cannot_escape_scratch_root_or_adopt_existing_data(self):
        with (
            patch("tests.integration.support._stop_server"),
            FixtureResources() as fixture,
        ):
            for name in ("../foreign", "/tmp/foreign", "", ".", ".."):
                with self.assertRaises(ValueError):
                    fixture.library(name)
                with self.assertRaises(ValueError):
                    fixture.server(name)
            (fixture.root / "existing").mkdir()
            with self.assertRaises(ValueError):
                fixture.library("existing")
            fixture.server("one")
            with self.assertRaises(ValueError):
                fixture.server("one")

    def test_manifest_cannot_redirect_cleanup_and_missing_manifest_is_safe(self):
        fixture = FixtureResources()
        library = fixture.library()

        def socket_path(path, suffix):
            return str(fixture.root / f"{path.name}-{suffix}.sock")

        with (
            patch("tests.integration.support.socket_path", side_effect=socket_path),
            patch("tests.integration.support.Tmux") as tmux,
        ):
            tmux.return_value.run.return_value = ""
            windows = library / "windows" / "one"
            windows.mkdir(parents=True)
            (windows / "runtime.json").write_text(json.dumps({"viewer_socket": "/foreign.sock"}))
            display = Path(socket_path(library, "view-one"))
            display.touch()
            second = Path(socket_path(library, "view-two"))
            second.touch()  # Missing runtime.json must not leak the owned display.
            symlink = Path(socket_path(library, "view-symlink"))
            symlink.symlink_to("/foreign.sock")
            fixture.close()
            self.assertEqual(
                [call.args[0] for call in tmux.call_args_list],
                [str(display), str(second), socket_path(library, "terminals")],
            )
            self.assertFalse(fixture.root.exists())

    def test_cleanup_attempts_every_resource_after_interruption(self):
        fixture = FixtureResources()
        library = fixture.library()
        server = fixture.server()
        first, second = Mock(), Mock()
        first.close.side_effect = KeyboardInterrupt()
        fixture.own_client(first)
        fixture.own_client(second)
        with (
            patch("tests.integration.support.cleanup_library") as cleanup,
            patch.object(server, "run", return_value="") as run,
            self.assertRaises(BaseExceptionGroup),
        ):
            fixture.close()
        first.close.assert_called_once()
        second.close.assert_called_once()
        cleanup.assert_called_once_with(library)
        run.assert_any_call("kill-server", check=False)
        self.assertFalse(fixture.root.exists())
        fixture.close()  # Idempotent after partial teardown failure.

    def test_body_failure_is_preserved_with_cleanup_failure_note(self):
        failure = RuntimeError("scenario failed")
        with self.assertRaises(RuntimeError) as raised, FixtureResources() as fixture:
            client = Mock()
            client.close.side_effect = OSError("cleanup failed")
            fixture.own_client(client)
            raise failure
        self.assertIs(raised.exception, failure)
        self.assertIn("Fixture cleanup also failed", failure.__notes__[0])
        self.assertFalse(fixture.root.exists())


class ServerCleanupTests(unittest.TestCase):
    def test_pid_query_failure_still_attempts_owned_server_stop(self):
        server = Mock()
        server.run.side_effect = [RuntimeError("query failed"), ""]
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            _stop_server(server)
        server.run.assert_any_call("kill-server", check=False)

    def test_shell_exit_wait_finishes_before_resource_files_are_removed(self):
        with FixtureResources() as fixture:
            server = fixture.server()
            sentinel = fixture.root / "history"
            sentinel.write_text("state")
            observed = []

            def alive(pid, sig):
                observed.append(sentinel.exists())
                raise ProcessLookupError()

            with (
                patch.object(server, "run", side_effect=["123", ""]),
                patch("tests.integration.support.os.kill", side_effect=alive),
            ):
                fixture.close()
            self.assertEqual(observed, [True])
            self.assertFalse(sentinel.exists())

    def test_state_only_wait_has_bounded_timeout_without_a_client(self):
        wait(None, lambda: True, "ready", timeout=0.1)
        with self.assertRaisesRegex(AssertionError, "not ready"):
            wait(None, lambda: False, "not ready", timeout=0.001)


if __name__ == "__main__":
    unittest.main()
