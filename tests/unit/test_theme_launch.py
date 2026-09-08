"""Theme selection survives clean launch environments without affecting shells."""

import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces import application, ghostty_launcher
from tmux_workspaces.cli import parser


class ThemeLaunchTests(unittest.TestCase):
    def test_ghostty_freezes_explicit_environment_and_xdg_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for explicit, configured in ((True, True), (False, True), (False, False)):
                with self.subTest(explicit=explicit, configured=configured):
                    env = {"XDG_CONFIG_HOME": str(root / "config")}
                    if configured:
                        env["TMUX_WORKSPACES_THEME"] = "environment colors.toml"
                    arguments = ["--theme", "explicit colors.toml"] if explicit else []
                    with patch.dict(os.environ, env, clear=True):
                        command = ghostty_launcher.launch_command(arguments, cwd=root)
                    shell = next(s for s in command if s.startswith("--command="))
                    argv = shlex.split(shell.removeprefix("--command=shell:"))[2:]
                    with patch.dict(os.environ, {}, clear=True):
                        options = parser().parse_args(argv)
                    expected = (
                        root / "explicit colors.toml"
                        if explicit
                        else root / "environment colors.toml"
                        if configured
                        else root / "config/tmux-workspaces/theme.toml"
                    )
                    self.assertEqual(options.theme, expected)
                    self.assertIsNone(options.terminal_colors)

    def test_private_sidebar_receives_outer_palette_and_absolute_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = parser().parse_args(["--no-keymap", "--data-dir", str(root / "library")])
            curses = Mock()
            curses.tigetnum.return_value = 8
            curses.error = type("CursesError", (Exception,), {})
            with (
                patch.dict(os.environ, {"TMUX_WORKSPACES_THEME": str(root / "colors.toml")}),
                patch.object(application, "check_startup"),
                patch.object(application, "load_curses", return_value=curses),
                patch.object(application, "Store"),
                patch.object(application, "Tmux"),
                patch.object(
                    application, "script_command", side_effect=RuntimeError("captured")
                ) as command,
                patch.object(application.subprocess, "run") as run,
                self.assertRaisesRegex(RuntimeError, "captured"),
            ):
                application.launch(args)
            child = parser().parse_args(command.call_args.args)
            self.assertEqual(child.theme, root / "colors.toml")
            self.assertEqual(child.terminal_colors, 8)
            curses.tigetnum.assert_called_once_with("colors")
            run.assert_not_called()
