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
from tmux_workspaces.controls import ghostty_bindings
from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap

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
