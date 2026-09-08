import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from tmux_workspaces.keymap import DEFAULT_KEYMAP

ROOT = Path(__file__).resolve().parents[2]


class KeymapCliTests(unittest.TestCase):
    def test_shipped_example_is_the_complete_default_map(self):
        self.assertEqual((ROOT / "integrations/keymap.toml").read_text(), DEFAULT_KEYMAP.to_toml())

    def test_exports_need_no_tty_servers_or_library_and_bad_config_fails_first(self):
        with tempfile.TemporaryDirectory(prefix="tw-keymap-cli-", dir="/tmp") as directory:
            root = Path(directory)
            config = root / "keys.toml"
            library = root / "library"
            config.write_text('prefix = "C-a"\n[bindings]\nnew-tab = ["u"]\n')
            env = dict(os.environ, TMUX_WORKSPACES_KEYMAP=str(config))

            def run(*args):
                return subprocess.run(
                    [sys.executable, str(ROOT / "run"), "--data-dir", str(library), *args],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

            result = run("--print-keymap", "help")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Viewer prefix: C-a", result.stdout)
            self.assertIn("u                    New tab", result.stdout)
            self.assertIn("hosting tmux never reach", result.stdout)
            config.write_text('[bindings]\nnew-tab = ["kill-server"]\n')
            invalid = run()
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("bindings.new-tab", invalid.stderr)
            self.assertIn(str(config), invalid.stderr)
            self.assertNotIn("Traceback", invalid.stderr)
            self.assertFalse(library.exists())
            default = run("--no-keymap", "--print-keymap", "toml")
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual(tomllib.loads(default.stdout)["prefix"], "C-g")


if __name__ == "__main__":
    unittest.main()
