"""Exercise command paths embedded in terminals before the package move."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tmux_workspaces.controls import Actions
from tmux_workspaces.entrypoints import application_argv

ROOT = Path(__file__).resolve().parents[2]


class EntrypointTests(unittest.TestCase):
    def test_source_symlink_and_legacy_commands_from_another_directory(self):
        with tempfile.TemporaryDirectory(prefix="tw-entry-", dir="/tmp") as directory:
            scratch = Path(directory)
            link = scratch / "workspace launcher"
            link.symlink_to(ROOT / "run")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.pop("PYTHONHOME", None)
            commands = [
                [str(link)],
                [sys.executable, str(ROOT / "experiments/workspace_viewer/viewer.py")],
                [sys.executable, str(ROOT / "scripts/tmux_plugin.py")],
                [sys.executable, str(ROOT / "scripts/ghostty_launcher.py")],
                [sys.executable, str(ROOT / "scripts/ui_preview.py")],
                application_argv(),
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [*command, "--help"],
                        cwd=scratch,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

    def test_old_action_helper_delivers_to_current_receiver(self):
        with tempfile.TemporaryDirectory(prefix="tw-action-", dir="/tmp") as directory:
            path = str(Path(directory) / "action.sock")
            receiver = Actions(path)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "experiments/workspace_viewer/viewer.py"),
                        "_action",
                        "--action-socket",
                        path,
                        "--action",
                        "new-tab",
                    ],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(receiver.pending()), ["new-tab"])
            finally:
                receiver.close()

    def test_action_helper_does_not_import_rendering_persistence_or_adapters(self):
        with tempfile.TemporaryDirectory(prefix="tw-lean-", dir="/tmp") as directory:
            path = str(Path(directory) / "action.sock")
            receiver = Actions(path)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import sys; from tmux_workspaces.cli import main; "
                        "sys.argv = ['tw', '_action', '--action-socket', sys.argv[1], "
                        "'--action', 'new-tab']; assert main() == 0; "
                        "assert not {'curses', 'sqlite3', 'urllib.request', "
                        "'tmux_workspaces.application'} & set(sys.modules)",
                        path,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(list(receiver.pending()), ["new-tab"])
            finally:
                receiver.close()

    def test_leaf_entry_import_does_not_load_action_transport(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import tmux_workspaces.cli; import tmux_workspaces.attachments; "
                "assert 'tmux_workspaces.controls' not in sys.modules; "
                "assert 'tmux_workspaces.application' not in sys.modules; "
                "assert 'curses' not in sys.modules",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_package_import_has_no_optional_adapter_or_application_side_effects(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, tmux_workspaces; "
                "assert 'tmux_workspaces.application' not in sys.modules; "
                "assert 'tmux_workspaces.source' not in sys.modules",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
