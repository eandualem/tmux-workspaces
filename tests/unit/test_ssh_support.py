import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from tests.integration.ssh_support import SshHost


class SshCleanupTests(unittest.TestCase):
    def host(self, directory):
        # Fake process handles let cleanup failures be tested without leaving a
        # listener running or sending a signal to any real process.
        host = SshHost.__new__(SshHost)
        host.root = Path(directory)
        host.clients = [Mock(), Mock()]
        host.process = Mock(pid=987654, poll=Mock(return_value=0))
        host.log = Mock()
        for name in ("host_key", "client_key", "host_key.pub", "client_key.pub", "authorized_keys"):
            (host.root / name).write_text("disposable test placeholder")
        return host

    def test_interrupted_daemon_wait_kills_reaps_and_removes_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch("os.killpg") as kill:
            host = self.host(directory)
            process, log = host.process, host.log
            process.wait.side_effect = [KeyboardInterrupt(), 0]
            with self.assertRaises(KeyboardInterrupt):
                host.close()
            self.assertEqual(
                kill.call_args_list,
                [call(987654, signal.SIGTERM), call(987654, signal.SIGKILL)],
            )
            self.assertEqual(process.wait.call_count, 2)
            self.assertIsNone(host.process)
            log.close.assert_called_once()
            self.assertEqual(list(host.root.iterdir()), [])
            host.close()  # Repeated ownership cleanup is harmless.

    def test_failed_client_still_closes_other_clients_daemon_and_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch("os.killpg") as kill:
            host = self.host(directory)
            clients = host.clients.copy()
            clients[-1].close.side_effect = OSError("client close failed")
            with self.assertRaisesRegex(OSError, "client close"):
                host.close()
            for client in clients:
                client.close.assert_called_once()
            kill.assert_called_once_with(987654, signal.SIGTERM)
            self.assertEqual(list(host.root.iterdir()), [])

    def test_daemon_timeout_escalates_but_preserves_scenario_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch("os.killpg") as kill:
            host = self.host(directory)
            process = host.process
            process.wait.side_effect = [subprocess.TimeoutExpired("sshd", 5), 0]
            host.close()
            self.assertEqual(kill.call_args_list[-1], call(987654, signal.SIGKILL))
            self.assertEqual(process.wait.call_count, 2)
            original = AssertionError("scenario failed")
            with patch.object(host, "close", side_effect=RuntimeError("cleanup failed")):
                host.__exit__(AssertionError, original, None)
            self.assertIn("cleanup failed", original.__notes__[0])


if __name__ == "__main__":
    unittest.main()
