import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces import ghostty_launcher as launcher
from tmux_workspaces import relaunch
from tmux_workspaces.cli import parser
from tmux_workspaces.controls import ghostty_bindings
from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap, keymap_source
from tmux_workspaces.supervisor import LaunchContext
from tmux_workspaces.theme import theme_path

ROOT = Path(__file__).resolve().parents[2]


class GhosttyLauncherTests(unittest.TestCase):
    def test_profile_matches_central_direct_shortcuts(self):
        self.assertEqual((ROOT / "integrations/ghostty.conf").read_text(), ghostty_bindings())

    def test_launch_captures_library_and_source_without_shared_preferences(self):
        with patch.dict(
            os.environ,
            {
                "TMUX": "/tmp/outer,source.sock,1,0",
                "TMUX_WORKSPACES_DATA_DIR": "/tmp/active library",
                "PATH": "/custom tools:/usr/bin:/bin",
                "TMUX_WORKSPACES_THEME": "/tmp/theme file.toml",
            },
            clear=True,
        ):
            command = launcher.launch_command([], cwd=Path("/tmp"))
        self.assertEqual(command[:3], ["open", "-na", str(launcher.DEFAULT_APP)])
        self.assertIn("PATH=/custom tools:/usr/bin:/bin", command)
        self.assertFalse(any("window-save-state" in value for value in command))
        self.assertFalse(any("config-default-files" in value for value in command))
        self.assertIn("--mouse-reporting=true", command)
        self.assertIn("--working-directory=" + str(Path("/tmp").resolve()), command)
        app_command = next(value for value in command if value.startswith("--command="))
        args = shlex.split(app_command.removeprefix("--command=shell:"))
        self.assertEqual(args[:2], [sys.executable, str(ROOT / "run")])
        self.assertEqual(
            args[2:],
            [
                "--data-dir",
                str(Path("/tmp/active library").resolve()),
                "--source-socket",
                str(Path("/tmp/outer,source.sock").resolve()),
                "--shortcut-hints=command",
                "--keymap-state",
                DEFAULT_KEYMAP.to_toml(),
                "--theme",
                "/tmp/theme file.toml",
                # The file this instance would read is named, so a window here
                # can offer the same selection back without this environment.
                "--keymap-source",
                str(Path.home() / ".config/tmux-workspaces/keymap.toml"),
            ],
        )

    def test_shell_command_preserves_literal_paths_and_viewer_arguments(self):
        with tempfile.TemporaryDirectory(prefix="tw-ghostty-", dir="/tmp") as directory:
            root = Path(directory) / "checkout with 'quotes'; $literal"
            root.mkdir()
            (root / "run").write_text(
                "import json, pathlib, sys\n"
                "pathlib.Path(__file__).with_suffix('.json').write_text(json.dumps(sys.argv[1:]))\n"
            )
            library = "library 'quoted'; $(touch UNEXPECTED) #literal"
            arguments = [
                "--data-dir",
                library,
                "--source-socket",
                "source 'socket'",
                "--backbone",
                "--backbone-data-dir",
                "adapter config",
                "--url",
                "http://127.0.0.1:9999/?x=$literal&y='value'",
            ]
            command = launcher.launch_command(arguments, root=root, cwd=root)
            shell_command = next(value for value in command if value.startswith("--command="))
            value = shell_command.removeprefix("--command=shell:")
            # Exercise Ghostty's actual shell wrappers, including the macOS
            # login-shell exec. Running only `sh -c value` missed the duplicate
            # exec regression that prevented the native app from opening.
            for wrapper in (
                ["/bin/sh", "-c", "exec " + value],
                ["/bin/bash", "--noprofile", "--norc", "-c", "exec -l " + value],
            ):
                with self.subTest(wrapper=wrapper[:2]):
                    subprocess.run(
                        wrapper,
                        cwd=root,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
            actual = json.loads((root / "run.json").read_text())
            self.assertEqual(actual[: len(arguments)], arguments)
            self.assertEqual(
                actual[len(arguments) :],
                [
                    "--data-dir",
                    str((root / library).resolve()),
                    "--source-socket",
                    str((root / "source 'socket'").resolve()),
                    "--shortcut-hints=command",
                    "--keymap-state",
                    DEFAULT_KEYMAP.to_toml(),
                    "--theme",
                    str(theme_path(cwd=root)),
                    "--keymap-source",
                    str(keymap_source(None)[0]),
                    "--backbone-data-dir",
                    str((root / "adapter config").resolve()),
                ],
            )
            self.assertFalse((root / "UNEXPECTED").exists())

    def test_backbone_config_directory_survives_a_fresh_application_environment(self):
        cwd = Path("/tmp/launcher caller").resolve()
        for explicit in (None, "explicit adapter"):
            with (
                self.subTest(explicit=explicit),
                patch.dict(os.environ, {"BACKBONE_DATA_DIR": "environment adapter"}, clear=True),
            ):
                arguments = ["--backbone"]
                if explicit:
                    arguments += ["--backbone-data-dir", explicit]
                command = launcher.launch_command(arguments, cwd=cwd)
            shell_command = next(value for value in command if value.startswith("--command="))
            argv = shlex.split(shell_command.removeprefix("--command=shell:"))[2:]
            with patch.dict(os.environ, {}, clear=True):
                options = launcher.viewer_parser().parse_args(argv)
            self.assertTrue(options.backbone)
            self.assertEqual(options.backbone_data_dir, cwd / (explicit or "environment adapter"))

    def test_normal_launch_does_not_read_backbone_environment_defaults(self):
        class IndependentEnvironment(dict):
            def get(self, key, default=None):
                if key.startswith("BACKBONE_"):
                    raise AssertionError("normal launch read adapter environment")
                return super().get(key, default)

        with patch.object(os, "environ", IndependentEnvironment()):
            command = launcher.launch_command([])
        shell_command = next(value for value in command if value.startswith("--command="))
        self.assertNotIn("--backbone-data-dir", shell_command)

    def test_a_viewer_in_this_profile_reopens_through_the_same_launcher(self):
        """Its keys are fixed here, so refreshing offers the dedicated launcher."""
        app = Path("/Applications/Ghostty Nightly.app")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            (home / "keys.toml").write_text('prefix = "C-a"\n')
            with patch.dict(
                os.environ,
                {"TMUX_WORKSPACES_KEYMAP": "keys.toml", "PATH": "/usr/bin:/bin"},
                clear=True,
            ):
                command = launcher.launch_command(
                    ["--backbone", "--data-dir", "my library"], app, cwd=home
                )
                argv = shlex.split(
                    next(v for v in command if v.startswith("--command=")).removeprefix(
                        "--command=shell:"
                    )
                )
                # The supervisor sees exactly what the launcher gave the viewer.
                context = LaunchContext(parser().parse_args(argv[2:]))
            self.assertIn("--keymap-state", argv)
            reopen = shlex.split(context.published)
            self.assertEqual(reopen[0], str(ROOT / "ghostty"))
            self.assertEqual(reopen[1:3], ["--ghostty-app", str(app)])
            self.assertIn(str(home / "my library"), reopen)
            self.assertIn("--backbone", reopen)
            # The file that produced those keys is named again, even though the
            # environment that selected it does not reach a new application.
            self.assertEqual(reopen[reopen.index("--keymap") + 1], str(home / "keys.toml"))
        self.assertNotIn("--keymap-state", context.published)
        self.assertNotIn("--shortcut-hints=command", context.published)
        self.assertNotIn("_window", context.published)

    def test_an_optional_default_keeps_the_directory_that_selected_it(self):
        """A new application does not inherit the environment that chose it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / "custom config"
            (config / "tmux-workspaces").mkdir(parents=True)
            home = root / "home"
            (home / ".config").mkdir(parents=True)
            with patch.dict(
                os.environ,
                {"HOME": str(home), "XDG_CONFIG_HOME": str(config), "PATH": "/usr/bin:/bin"},
                clear=True,
            ):
                command = launcher.launch_command(["--data-dir", str(root / "library")], cwd=root)
            argv = shlex.split(
                next(v for v in command if v.startswith("--command=")).removeprefix(
                    "--command=shell:"
                )
            )
            selected = config / "tmux-workspaces/keymap.toml"
            self.assertEqual(argv[argv.index("--keymap-source") + 1], str(selected))
            self.assertNotIn("--keymap", argv)
            # LaunchServices starts the application without that variable.
            with patch.dict(os.environ, {"HOME": str(home)}, clear=True):
                context = LaunchContext(parser().parse_args(argv[2:]))
                child = context.child_args(root / "handover.json", None)
            self.assertEqual(context.keymap_selector, selected)
            self.assertFalse(context.keymap_required, "an absent default became an error")
            # Keys here are fixed, so this window reloads nothing at all.
            self.assertIn("--manual-reopen", child)
            self.assertNotIn("--keymap-source", child)
            self.assertNotIn("--keymap-required", child)
            # The command names that directory rather than an explicit file, so
            # a default created later is still picked up, and it stays one
            # ordinary argument list that runs without a shell.
            argv = shlex.split(context.published)
            self.assertEqual(argv[:2], ["env", f"XDG_CONFIG_HOME={config}"])
            self.assertEqual(argv[2], str(ROOT / "ghostty"))
            self.assertNotIn("--keymap", argv)
            self.assertIn(str(root / "library"), argv)
            self.assertFalse(selected.exists())

    def test_a_fixed_profile_window_shows_only_what_its_supervisor_published(self):
        """The window's own arguments are private and must never be offered."""
        request = relaunch.Request(
            Path("/tmp/unused-instance"),
            manual_reopen=True,
            profile_hints=True,
            reopen_command="/checkout/ghostty --data-dir '/lib rary'",
        )
        private = [
            sys.executable,
            str(ROOT / "run"),
            "_sidebar",
            "--data-dir",
            "/lib rary",
            "--viewer-socket",
            "/tmp/private-view.sock",
            "--action-socket",
            "/tmp/private-actions.sock",
            "--shell-socket",
            "/tmp/shared-shells.sock",
            "--instance-dir",
            "/lib rary/windows/abcdef",
            "--keymap-state",
            'prefix = "C-b"\n',
        ]
        with patch.object(sys, "orig_argv", private):
            self.assertEqual(request.manual_command(), "/checkout/ghostty --data-dir '/lib rary'")
        self.assertIn("reopened by hand", request.check())
        self.assertFalse(request.submit({"workspace": "w1", "tab": "", "leaf": "", "focus": False}))

    def test_dry_run_never_launches_an_application(self):
        with patch.object(subprocess, "call") as call, redirect_stdout(io.StringIO()) as output:
            result = launcher.main(["--dry-run", "--data-dir", "/tmp/trial"])
        self.assertEqual(result, 0)
        self.assertEqual(shlex.split(output.getvalue())[0], "open")
        call.assert_not_called()

    def test_symlink_entry_runs_from_another_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="tw-ghostty-link-", dir="/tmp") as directory:
            link = Path(directory) / "workspaces ghostty"
            link.symlink_to(ROOT / "ghostty")
            result = subprocess.run(
                [str(link), "--dry-run", "--", "--data-dir", "relative library"],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            command = shlex.split(result.stdout)
            self.assertIn("--working-directory=" + str(Path(directory).resolve()), command)
            self.assertIn("--keybind=super+t=csi:9001~", command)

    def test_custom_profile_and_new_surface_share_one_immutable_map(self):
        with tempfile.TemporaryDirectory(prefix="tw-keymap-ghostty-", dir="/tmp") as directory:
            cwd = Path(directory)
            path = cwd / "custom map.toml"
            path.write_text(
                'prefix = "C-a"\n[bindings]\nnew-tab = ["u"]\n'
                '[direct]\nnew-tab = ["super+alt+t"]\nrename-tab = []\n'
            )
            command = launcher.launch_command(["--keymap", path.name], cwd=cwd)
            self.assertIn("--keybind=super+alt+t=csi:9001~", command)
            self.assertIn("--keybind=super+t=ignore", command)
            self.assertIn("--keybind=super+r=ignore", command)
            self.assertNotIn("--keybind=super+r=csi:9011~", command)
            shell = next(arg for arg in command if arg.startswith("--command="))
            options = launcher.viewer_parser().parse_args(
                shlex.split(shell.removeprefix("--command=shell:"))[2:]
            )
            path.write_text('prefix = "C-z"\n')
            from tmux_workspaces.cli import effective_keymap

            frozen = effective_keymap(options)
            self.assertEqual(frozen.prefix, "C-a")
            self.assertEqual(frozen.bindings["new-tab"], ("u",))
            self.assertEqual(frozen.direct["rename-tab"], ())
            self.assertEqual(frozen, Keymap.from_dict(tomllib.loads(options.keymap_state)))


if __name__ == "__main__":
    unittest.main()
