import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.application import launch
from tmux_workspaces.attachments import attachment_hosts_viewer
from tmux_workspaces.cli import default_data_dir, default_source_socket, parser
from tmux_workspaces.display import Display
from tmux_workspaces.model import Model
from tmux_workspaces.targets import session_target
from tmux_workspaces.tmux import clean_env


class LauncherTests(unittest.TestCase):
    def test_default_library_is_independent_and_respects_overrides(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(default_data_dir(), Path.home() / ".local/share/tmux-workspaces")
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/custom-data"}, clear=True):
            self.assertEqual(default_data_dir(), Path("/tmp/custom-data/tmux-workspaces"))
        with patch.dict(
            os.environ,
            {"TMUX_WORKSPACES_DATA_DIR": "/tmp/library", "XDG_DATA_HOME": "/tmp/ignored"},
            clear=True,
        ):
            self.assertEqual(default_data_dir(), Path("/tmp/library"))

    def test_source_socket_uses_invoking_server_before_private_env_is_cleared(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/source,one.sock,123,0"}, clear=True):
            self.assertEqual(default_source_socket(), "/tmp/source,one.sock")
        with patch.dict(os.environ, {"TMUX_TMPDIR": "/tmp/runtime"}, clear=True):
            self.assertEqual(default_source_socket(), f"/tmp/runtime/tmux-{os.getuid()}/default")

    def test_backbone_options_require_explicit_adapter(self):
        for arguments in (["--url", "http://127.0.0.1:9"], ["--backbone-data-dir", "/tmp/unused"]):
            with (
                self.subTest(arguments=arguments),
                self.assertRaisesRegex(ValueError, "--backbone"),
            ):
                launch(parser().parse_args(arguments))
        args = parser().parse_args([])
        self.assertFalse(args.backbone)
        self.assertIsNone(args.backbone_data_dir)

    def test_saved_attachment_uses_original_socket_and_raw_name(self):
        display = Display(
            "/tmp/view.sock", "/tmp/new-source.sock", "%0", "/tmp/shells.sock", "/tmp/action.sock"
        )
        model = Model.initial()
        name = "-Ops [α] 'x'; $value"
        model.attach(name, "/tmp/original source.sock")
        argv = shlex.split(display._leaf_command(model.pane))
        args = parser().parse_args(argv[2:])
        self.assertEqual(args.agent, name)
        self.assertEqual(args.source_socket, "/tmp/original source.sock")
        for socket in (display.tmux.socket, display.shells.tmux.socket):
            model.attach(name, socket)
            with self.assertRaisesRegex(ValueError, "external source socket"):
                display._leaf_command(model.pane)

    def test_symlink_launcher_finds_checkout_from_another_working_directory(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "tmux workspaces"
            link.symlink_to(root / "run")
            result = subprocess.run(
                [str(link), "--help"], capture_output=True, text=True, cwd=directory, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--backbone", result.stdout)

    @unittest.skipUnless(shutil.which("tmux"), "tmux required for isolated recursion test")
    def test_host_recursion_guard_checks_panes_across_linked_and_grouped_sessions(self):
        with tempfile.TemporaryDirectory(prefix="tw-host-", dir="/tmp") as directory:
            socket = str(Path(directory) / "tmux_workspaces.source.sock")

            def tmux(*args, check=True):
                return subprocess.run(
                    ["tmux", "-S", socket, *args],
                    env=clean_env(),
                    check=check,
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()

            try:
                tmux(
                    "-f", "/dev/null", "new-session", "-d", "-s", "host", "-n", "shared", "/bin/sh"
                )
                host_pane = tmux("display-message", "-p", "-t", "=host:shared", "#{pane_id}")
                tmux("new-session", "-d", "-s", "grouped host", "-t", "host")
                tmux("new-session", "-d", "-s", "linked host", "/bin/sh")
                tmux("link-window", "-s", "=host:shared", "-t", "=linked host:2")
                tmux("new-session", "-d", "-s", "unrelated", "/bin/sh")
                args = parser().parse_args(
                    [
                        "_leaf",
                        "--source-socket",
                        socket,
                        "--host-socket",
                        socket,
                        "--host-pane",
                        host_pane,
                    ]
                )
                for name in ("host", "grouped host", "linked host"):
                    with self.subTest(name=name):
                        self.assertTrue(attachment_hosts_viewer(args, session_target(name)))
                self.assertFalse(attachment_hosts_viewer(args, session_target("unrelated")))
                args.host_socket = str(Path(directory) / "another.sock")
                with patch(
                    "tmux_workspaces.attachments.Tmux",
                    side_effect=AssertionError("queried another server"),
                ):
                    self.assertFalse(attachment_hosts_viewer(args, session_target("host")))
            finally:
                tmux("kill-server", check=False)


if __name__ == "__main__":
    unittest.main()
