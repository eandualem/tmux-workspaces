import json
import os
import pty
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces import tmux_plugin as plugin

ROOT = Path(__file__).resolve().parents[2]


class PluginTests(unittest.TestCase):
    def test_old_tmux_dollar_printing_preserves_literal_backslashes(self):
        printed = r"\$name \${name} \$_name \\$literal $(command) $1 \n"
        expected = r"$name ${name} $_name \$literal $(command) $1 \n"
        plugin._escapes_dollars.cache_clear()
        with patch.object(plugin, "_tmux", side_effect=[printed, r"\$tmux_workspaces_probe"]):
            self.assertEqual(plugin.tmux("fixture.sock", "show-options"), expected)
        plugin._escapes_dollars.cache_clear()

    def test_modern_tmux_literal_dollar_backslashes_are_not_removed(self):
        printed = r"\$name ${name} $_name \\$literal $(command) $1 \n"
        plugin._escapes_dollars.cache_clear()
        with patch.object(plugin, "_tmux", side_effect=[printed, "$tmux_workspaces_probe"]):
            self.assertEqual(plugin.tmux("fixture.sock", "show-options"), printed)
        plugin._escapes_dollars.cache_clear()

    def test_install_outside_tmux_refuses_implicit_server(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(plugin, "tmux") as tmux:
            with self.assertRaisesRegex(ValueError, "run-shell or TPM"):
                plugin.install()
            tmux.assert_not_called()

    def test_default_launch_ignores_adapter_settings_and_clears_outer_identity(self):
        values = {"backbone-data-dir": "/unused", "url": "http://unused"}
        with (
            patch.object(plugin, "option", side_effect=lambda socket, name: values.get(name, "")),
            patch.dict(os.environ, {"TMUX": "outer", "TMUX_PANE": "%1", "TERM": "screen"}),
            patch.object(os, "execve") as execute,
        ):
            plugin.launch("/tmp/test-outer.sock")
        command, env = execute.call_args.args[1:]
        self.assertEqual(
            command,
            [
                str(ROOT / "run"),
                "--source-socket",
                "/tmp/test-outer.sock",
                "--host-socket",
                "/tmp/test-outer.sock",
                "--host-pane",
                "%1",
            ],
        )
        self.assertNotIn("TMUX", env)
        self.assertNotIn("TMUX_PANE", env)
        self.assertEqual(env["TERM"], "screen")

    @unittest.skipUnless(shutil.which("tmux"), "tmux required for isolated plugin test")
    def test_real_key_launch_preserves_paths_options_and_outer_pane(self):
        # Short private socket paths also fit macOS's Unix socket limit.
        with tempfile.TemporaryDirectory(prefix="tw-plugin-", dir="/tmp") as directory:
            scratch = Path(directory)
            socket = str(scratch / "outer.sock")
            repo = scratch / "plugin 'quoted' ; $literal #{pane_current_path}"
            repo.mkdir()
            shutil.copytree(ROOT / "tmux_workspaces", repo / "tmux_workspaces")
            (repo / "scripts").mkdir()
            shutil.copy(ROOT / "scripts/tmux_plugin.py", repo / "scripts/tmux_plugin.py")
            shutil.copy(ROOT / "tmux-workspaces.tmux", repo / "tmux-workspaces.tmux")
            (repo / "run").write_text(
                "#!" + sys.executable + "\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(__file__).with_name('launched.json').write_text(json.dumps({\n"
                "    'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
                "    'tmux': os.environ.get('TMUX'), 'pane': os.environ.get('TMUX_PANE')\n"
                "}))\n"
            )
            (repo / "run").chmod(0o700)
            env = os.environ.copy()
            env.pop("TMUX", None)
            env.pop("TMUX_PANE", None)
            env["TERM"] = "xterm-256color"
            env["SHELL"] = "/bin/sh"

            def tmux(*args):
                return subprocess.run(
                    ["tmux", "-S", socket, *args],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout

            client = None
            master = None
            try:
                tmux("-f", "/dev/null", "new-session", "-d", "-s", "host", "-c", str(scratch))
                pane = tmux("display-message", "-p", "#{pane_id}:#{pane_pid}").strip()
                settings = {
                    "key": "M-w",
                    "data-dir": str(scratch / "data 'literal' ; $(touch SHOULD_NOT_EXIST) #{}"),
                    "backbone": "on",
                    "backbone-data-dir": str(scratch / "adapter config"),
                    "url": (
                        "http://127.0.0.1:9999/path?literal=$value&other='quote'"
                        r"&braced=${name}&underscore=$_name&slash=\$name&digits=$1"
                    ),
                }
                for name, value in settings.items():
                    tmux("set-option", "-g", "@tmux-workspaces-" + name, value)
                before = tmux("show-options", "-g"), tmux("show-options", "-gw")
                # run-shell expands tmux formats before its shell executes.
                tmux(
                    "run-shell", shlex.quote(str(repo / "tmux-workspaces.tmux")).replace("#", "##")
                )
                binding = "\n".join(
                    line
                    for line in tmux("list-keys", "-T", "prefix").splitlines()
                    if "M-w " in line
                )
                self.assertIn("new-window", binding)
                self.assertEqual(before, (tmux("show-options", "-g"), tmux("show-options", "-gw")))
                master, slave = pty.openpty()
                client = subprocess.Popen(
                    ["tmux", "-S", socket, "attach-session", "-t", "=host:"],
                    env=env,
                    stdin=slave,
                    stdout=slave,
                    stderr=slave,
                )
                os.close(slave)
                deadline = time.monotonic() + 8
                while not tmux("list-clients").strip():
                    if time.monotonic() > deadline:
                        self.fail("private test client did not attach")
                    time.sleep(0.02)
                # Default prefix from our empty tmux config, then configured M-w.
                # Keep these separate gestures: the host's default paste
                # heuristic may bypass a binding when both arrive together.
                os.write(master, b"\x02")
                deadline = time.monotonic() + 8
                while tmux("list-clients", "-F", "#{client_prefix}").strip() != "1":
                    if time.monotonic() > deadline:
                        self.fail("private test client did not enter prefix mode")
                    ready, _, _ = select.select([master], [], [], 0.05)
                    if ready:
                        os.read(master, 65536)
                os.write(master, b"\x1bw")
                launched = repo / "launched.json"
                output = bytearray()
                deadline = time.monotonic() + 8
                while not launched.exists() and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.05)
                    if ready:
                        output.extend(os.read(master, 65536))
                self.assertTrue(launched.exists(), f"plugin did not launch: {output!r}")
                actual = json.loads(launched.read_text())
                self.assertEqual(
                    actual["args"],
                    [
                        "--source-socket",
                        socket,
                        "--host-socket",
                        socket,
                        "--host-pane",
                        "%1",
                        "--data-dir",
                        settings["data-dir"],
                        "--backbone",
                        "--backbone-data-dir",
                        settings["backbone-data-dir"],
                        "--url",
                        settings["url"],
                    ],
                )
                self.assertEqual(Path(actual["cwd"]).resolve(), scratch.resolve())
                self.assertIsNone(actual["tmux"])
                self.assertIsNone(actual["pane"])
                self.assertEqual(
                    pane,
                    tmux(
                        "display-message", "-p", "-t", "=host:0", "#{pane_id}:#{pane_pid}"
                    ).strip(),
                )
                self.assertEqual(before, (tmux("show-options", "-g"), tmux("show-options", "-gw")))
                self.assertFalse((scratch / "SHOULD_NOT_EXIST").exists())
            finally:
                subprocess.run(
                    ["tmux", "-S", socket, "kill-server"],
                    env=env,
                    capture_output=True,
                )
                if client is not None:
                    client.wait(timeout=5)
                if master is not None:
                    os.close(master)


if __name__ == "__main__":
    unittest.main()
