"""Compatibility failures occur before state writes or tmux server commands."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces import application, preflight
from tmux_workspaces.cli import parser

ROOT = Path(__file__).resolve().parents[2]


class VersionTests(unittest.TestCase):
    def test_missing_tmux_never_spawns_a_command(self):
        with (
            patch.object(preflight.shutil, "which", return_value=None),
            patch.object(preflight.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "tmux not found; tmux 3.3"):
                preflight.check_tmux()
            run.assert_not_called()

    def test_tmux_versions_use_only_the_serverless_version_flag(self):
        for version, accepted in (
            ("tmux 3.2a", False),
            ("tmux 3.3", True),
            ("tmux 3.7c", True),
            ("tmux next-3.5", True),
            ("tmux 3.4-rc1", True),
            ("tmux 4.0", True),
            ("tmux master", False),
            ("not tmux", False),
        ):
            with (
                self.subTest(version=version),
                patch.object(preflight.shutil, "which", return_value="/fixture/bin/tmux"),
                patch.object(
                    preflight.subprocess,
                    "run",
                    return_value=Mock(stdout=version + "\n", returncode=0),
                ) as run,
            ):
                if accepted:
                    self.assertEqual(preflight.check_tmux(), version)
                else:
                    with self.assertRaisesRegex(RuntimeError, "tmux 3.3 or newer") as raised:
                        preflight.check_tmux()
                    self.assertIn(version, str(raised.exception))
                self.assertEqual(run.call_args.args[0], ["/fixture/bin/tmux", "-V"])
                self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_failed_or_hung_version_command_is_actionable(self):
        for failure in (OSError("cannot execute"), subprocess.TimeoutExpired("tmux", 5)):
            with (
                self.subTest(failure=failure),
                patch.object(preflight.shutil, "which", return_value="/fixture/tmux"),
                patch.object(preflight.subprocess, "run", side_effect=failure),
                self.assertRaisesRegex(RuntimeError, "Cannot run tmux -V"),
            ):
                preflight.check_tmux()


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.input = patch.object(sys, "stdin", Mock(isatty=Mock(return_value=True)))
        self.output = patch.object(sys, "stdout", Mock(isatty=Mock(return_value=True)))
        self.input.start()
        self.output.start()
        self.addCleanup(self.input.stop)
        self.addCleanup(self.output.stop)
        self.curses = Mock()
        self.curses.error = type("CursesError", (Exception,), {})
        self.curses.tigetnum.side_effect = lambda name: {"colors": 8, "pairs": 64}[name]
        self.curses.tigetstr.return_value = b"capability"

    def test_missing_curses_and_missing_color_api(self):
        with (
            patch.object(preflight.importlib, "import_module", side_effect=ImportError("_curses")),
            self.assertRaisesRegex(RuntimeError, "curses support is unavailable"),
        ):
            preflight.load_curses()
        self.curses.use_default_colors = None
        with (
            patch.object(preflight.importlib, "import_module", return_value=self.curses),
            self.assertRaisesRegex(RuntimeError, "lacks required terminal/color/mouse"),
        ):
            preflight.load_curses()

    def test_missing_term_and_nonterminal_fail_before_curses(self):
        with patch.object(preflight, "load_curses") as load, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TERM is missing"):
                preflight.check_terminal()
            sys.stdin.isatty.return_value = False
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                preflight.check_terminal()
            load.assert_not_called()

    def test_unknown_term_and_color_capabilities(self):
        with (
            patch.object(preflight, "load_curses", return_value=self.curses),
            patch.dict(os.environ, {"TERM": "fixture-term"}),
        ):
            self.curses.setupterm.side_effect = self.curses.error("unknown terminal")
            with self.assertRaisesRegex(RuntimeError, "fixture-term.*no usable terminfo"):
                preflight.check_terminal()
            self.curses.setupterm.side_effect = None
            for colors, pairs in ((0, 0), (7, 64), (8, 4)):
                self.curses.tigetnum.side_effect = {"colors": colors, "pairs": pairs}.__getitem__
                with self.assertRaisesRegex(RuntimeError, "at least 8 colors and 5 pairs"):
                    preflight.check_terminal()
            self.curses.tigetnum.side_effect = lambda name: {"colors": 8, "pairs": 64}[name]
            preflight.check_terminal()  # Existing eight-color fallback remains supported.
            self.curses.wrapper.assert_not_called()
            self.curses.start_color.assert_not_called()
            self.curses.tigetstr.return_value = None
            with self.assertRaisesRegex(RuntimeError, "cursor positioning"):
                preflight.check_terminal()


class StartupBoundaryTests(unittest.TestCase):
    def test_preflight_failure_cannot_create_or_change_a_library_or_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for existing in (False, True):
                library = root / str(existing)
                if existing:
                    library.mkdir()
                    (library / "layouts.db").write_bytes(b"untouched fixture bytes")
                args = parser().parse_args(["_window", "--no-keymap", "--data-dir", str(library)])
                with (
                    patch.object(
                        application, "check_startup", side_effect=RuntimeError("unsupported")
                    ),
                    patch.object(application, "Store") as store,
                    patch.object(application, "socket_path") as sockets,
                ):
                    with self.assertRaisesRegex(RuntimeError, "unsupported"):
                        application.window_main(args)
                    store.assert_not_called()
                    sockets.assert_not_called()
                if existing:
                    self.assertEqual(
                        (library / "layouts.db").read_bytes(), b"untouched fixture bytes"
                    )
                    self.assertEqual(len(list(library.iterdir())), 1)
                else:
                    self.assertFalse(library.exists())

    def test_help_and_generated_diagnostics_need_no_tmux_terminal_or_curses(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"PATH": directory, "HOME": directory, "LANG": "C"}
            commands = [
                ["run", "--help"],
                ["run", "--no-keymap", "--print-keymap", "help"],
                ["ghostty", "--help"],
                ["ghostty", "--dry-run", "--no-keymap"],
            ]
            for name, *args in commands:
                with self.subTest(name=name, args=args):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / name), *args],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout)
                    self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_preview_preflight_happens_before_seed(self):
        from tmux_workspaces import ui_preview

        with (
            patch.object(sys, "argv", ["preview", "--terminal"]),
            patch.object(
                preflight, "check_startup", side_effect=RuntimeError("unsupported fixture")
            ),
            patch.object(ui_preview, "seed") as seed,
            patch.object(sys, "stderr", io.StringIO()),
        ):
            self.assertEqual(ui_preview.main(), 1)
            seed.assert_not_called()

    def test_preview_module_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "tmux_workspaces.ui_preview", "--terminal"],
                cwd=ROOT,
                env={"PATH": directory, "HOME": directory},
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("tmux not found", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_ghostty_failures_offer_current_terminal_and_dry_run_skips_checks(self):
        from tmux_workspaces import ghostty_launcher as ghostty

        with (
            patch.object(sys, "platform", "linux"),
            self.assertRaisesRegex(RuntimeError, "requires macOS.*./run"),
        ):
            ghostty.check_launch()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(sys, "platform", "darwin"),
            self.assertRaisesRegex(RuntimeError, "missing or not executable.*./run"),
        ):
            ghostty.check_launch(Path(directory) / "Absent.app")
        with (
            patch.object(ghostty, "check_launch"),
            patch.object(ghostty, "launch_command", return_value=["fixture-open"]),
            patch.object(ghostty.subprocess, "call", return_value=7),
            patch.object(sys, "stderr", io.StringIO()) as error,
        ):
            self.assertEqual(ghostty.main([]), 7)
            self.assertIn("exit 7", error.getvalue())
            self.assertIn("./run", error.getvalue())


if __name__ == "__main__":
    unittest.main()
