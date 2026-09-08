"""Public launchers reject older interpreters before importing runtime code."""

import ast
import contextlib
import io
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from tmux_workspaces import bootstrap

ROOT = Path(__file__).resolve().parents[2]
LAUNCHERS = (
    "run",
    "ghostty",
    "preview",
    "scripts/ghostty_launcher.py",
    "scripts/ui_preview.py",
    "scripts/tmux_plugin.py",
    "experiments/workspace_viewer/viewer.py",
)


class BootstrapTests(unittest.TestCase):
    def test_python_floor_reports_detected_version_and_remedy(self):
        for version in ((3, 6, 15), (3, 9, 6), (3, 10, 19)):
            with self.subTest(version=version), mock.patch.object(sys, "version_info", version):
                with self.assertRaises(RuntimeError) as raised:
                    bootstrap.require_python()
                self.assertIn(".".join(map(str, version)), str(raised.exception))
                self.assertIn("Python 3.11+", str(raised.exception))
                self.assertIn("Install Python", str(raised.exception))
        for version in ((3, 11, 0), (3, 14, 0), (4, 0, 0)):
            with self.subTest(version=version), mock.patch.object(sys, "version_info", version):
                bootstrap.require_python()

    def test_unsupported_dispatch_never_imports_runtime(self):
        for entry in (
            bootstrap.main,
            bootstrap.ghostty_main,
            bootstrap.preview_main,
            bootstrap.plugin_main,
        ):
            with (
                self.subTest(entry=entry.__name__),
                mock.patch.object(sys, "version_info", (3, 10, 0)),
                mock.patch.object(bootstrap.importlib, "import_module") as importer,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                self.assertEqual(entry(), 2)
                importer.assert_not_called()
                self.assertTrue(stderr.getvalue().startswith("tmux-workspaces: Python 3.10.0"))
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_supported_dispatch_retains_result_and_ghostty_arguments(self):
        arguments = ["--help"]
        with mock.patch.object(bootstrap.importlib, "import_module") as importer:
            importer.return_value.main.return_value = 7
            self.assertEqual(bootstrap.main(), 7)
            importer.assert_called_once_with(".cli", "tmux_workspaces")
            importer.return_value.main.assert_called_once_with()
            importer.reset_mock()
            self.assertEqual(bootstrap.ghostty_main(arguments), 7)
            importer.assert_called_once_with(".ghostty_launcher", "tmux_workspaces")
            importer.return_value.main.assert_called_once_with(arguments)

    def test_bootstrap_and_entrypoints_parse_on_python_36(self):
        for filename in (
            *LAUNCHERS,
            "tmux_workspaces/bootstrap.py",
            "tmux_workspaces/__init__.py",
            "tmux_workspaces/__main__.py",
        ):
            with self.subTest(filename=filename):
                ast.parse((ROOT / filename).read_text(), filename, feature_version=(3, 6))

    def test_every_public_entry_rejects_old_python_without_traceback(self):
        with (ROOT / "pyproject.toml").open("rb") as stream:
            console = tomllib.load(stream)["project"]["scripts"]["tmux-workspaces"]
        self.assertEqual(console, "tmux_workspaces.bootstrap:main")
        entries = [
            f"runpy.run_path({str(ROOT / path)!r}, run_name='__main__')" for path in LAUNCHERS
        ]
        entries += [
            "runpy.run_module('tmux_workspaces', run_name='__main__')",
            "from tmux_workspaces.bootstrap import main; sys.exit(main())",
        ]
        for entry in entries:
            with self.subTest(entry=entry):
                # Install the simulated version after loading runpy itself; failures
                # must come from our guard, not an older stdlib simulation.
                program = (
                    "import runpy, sys; sys.version_info = (3, 9, 6); "
                    "sys.argv = ['tmux-workspaces', '--help']; " + entry
                )
                result = subprocess.run(
                    [sys.executable, "-c", program],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("Python 3.9.6", result.stderr)
                self.assertIn("Python 3.11+", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_importing_bootstrap_keeps_package_inert(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import tmux_workspaces.bootstrap; "
                "assert not {'curses', 'tomllib', 'sqlite3', 'subprocess', "
                "'tmux_workspaces.cli', 'tmux_workspaces.application', "
                "'tmux_workspaces.ghostty_launcher'} & set(sys.modules)",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
