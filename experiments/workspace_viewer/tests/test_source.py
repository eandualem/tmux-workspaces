import contextlib
import http.client
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source import NoRedirect, Source, connection, valid_agent
from targets import session_target, valid_session
from terminal import Display, clean_env


class SourceTests(unittest.TestCase):
    def test_connection_reads_only_existing_database_and_named_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory)
            (path / ".env").write_text(
                'BACKBONE_API_KEY="test-only-key" # comment\nOTHER_SECRET=private\n'
            )
            with contextlib.closing(sqlite3.connect(path / "backbone.db")) as db, db:
                db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
                db.execute("INSERT INTO settings VALUES ('backbone.port', '9876')")
            before = (path / "backbone.db").read_bytes()
            self.assertEqual(connection(path, None), ("http://127.0.0.1:9876", "test-only-key"))
            self.assertEqual((path / "backbone.db").read_bytes(), before)
            self.assertNotIn("OTHER_SECRET", os.environ)

    def test_source_failure_keeps_roster_but_marks_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agents.json"
            path.write_text(
                json.dumps(
                    [
                        {"name": "worker", "state": "busy"},
                        {"name": "not-agent", "configured": False},
                        {"name": "=bad:target"},
                    ]
                )
            )
            source = Source("/unused/demo.sock", demo=path)
            source.refresh()
            self.assertEqual(list(source.snapshot()[0]), ["worker"])
            path.write_text("invalid")
            source.refresh()
            agents, error = source.snapshot()
            self.assertEqual(agents["worker"]["state"], "busy")
            self.assertIn("stale", error)

    def test_default_reads_only_the_explicit_tmux_socket(self):
        process = subprocess.CompletedProcess(
            [], 0, stdout="ordinary shell\n=leading\ncafé\nunsafe:window\n", stderr=""
        )
        with (
            patch("source.subprocess.run", return_value=process) as run,
            patch("source.connection", side_effect=AssertionError("Backbone config read")),
            patch("source.urllib.request.build_opener", side_effect=AssertionError("HTTP used")),
            patch("source.sqlite3.connect", side_effect=AssertionError("Database read")),
            patch.object(Path, "read_text", side_effect=AssertionError("Config file read")),
            patch.dict(os.environ, {"BACKBONE_API_KEY": "not-for-session-discovery"}),
        ):
            source = Source("/explicit/private.sock")
            source.refresh()
        sessions, error = source.snapshot()
        self.assertEqual(source.socket, "/explicit/private.sock")
        self.assertEqual(set(sessions), {"ordinary shell", "=leading", "café"})
        self.assertEqual(error, "")
        self.assertTrue(all(item["online"] for item in sessions.values()))
        self.assertTrue(all(item["origin"] == "tmux" for item in sessions.values()))
        self.assertEqual(
            run.call_args.args[0],
            ["tmux", "-S", source.socket, "list-sessions", "-F", "#{session_name}"],
        )
        self.assertNotIn("BACKBONE_API_KEY", run.call_args.kwargs["env"])

    def test_demo_reads_fixture_without_tmux_or_backbone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            path.write_text(json.dumps([{"name": "ordinary shell", "online": False}]))
            with (
                patch("source.connection", side_effect=AssertionError("Backbone config read")),
                patch("source.subprocess.run", side_effect=AssertionError("tmux queried")),
                patch("source.urllib.request.build_opener", side_effect=AssertionError("HTTP")),
            ):
                source = Source("/unused", backbone_data_dir=Path(directory), demo=path)
                source.refresh()
            sessions, error = source.snapshot()
            self.assertEqual(list(sessions), ["ordinary shell"])
            self.assertFalse(sessions["ordinary shell"]["online"])
            self.assertEqual(error, "")

    def test_absent_server_and_query_failures_are_nonfatal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Source(str(Path(directory) / "missing.sock"))
            with patch(
                "source.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="missing"),
            ):
                source.refresh()
            self.assertEqual(source.snapshot(), ({}, ""))
            for failure in (OSError("missing executable"), subprocess.TimeoutExpired("tmux", 3)):
                with patch("source.subprocess.run", side_effect=failure):
                    source.refresh()
                self.assertEqual(source.snapshot(), ({}, "Session list unavailable"))

    def test_adapter_configuration_failure_keeps_generic_sessions(self):
        process = subprocess.CompletedProcess([], 0, stdout="ordinary shell\n", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".env").write_text('BACKBONE_API_KEY="unterminated\n')
            source = Source("/private.sock", backbone_data_dir=path)
            with patch("source.subprocess.run", return_value=process):
                source.refresh()
            sessions, error = source.snapshot()
            self.assertEqual(list(sessions), ["ordinary shell"])
            self.assertIn("Backbone unavailable", error)
            (path / ".env").unlink()
            (path / "backbone.db").write_bytes(b"invalid SQLite database")
            with patch("source.subprocess.run", return_value=process):
                source.refresh()
            self.assertEqual(list(source.snapshot()[0]), ["ordinary shell"])
            self.assertIn("Backbone unavailable", source.snapshot()[1])

    def test_adapter_overlay_retains_offline_roster_and_recovers_after_failure(self):
        process = subprocess.CompletedProcess([], 0, stdout="ordinary shell\nworker\n", stderr="")
        items = [
            {"name": "worker", "state": "busy", "online": False},
            {"name": "parked", "state": "offline", "online": True},
            {"name": "bad-state", "state": {"unexpected": "value"}},
            {"name": "ignored", "configured": False},
            {"name": ["not", "a", "name"]},
            {"name": "not an agent"},
        ]
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(json.dumps({"items": items}).encode()),
            urllib.error.URLError("unavailable"),
            io.BytesIO(b'{"items": []}'),
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("source.subprocess.run", return_value=process),
            patch("source.urllib.request.build_opener", return_value=opener) as build_opener,
            patch.dict(os.environ, {"BACKBONE_API_KEY": "test-only-key"}),
        ):
            source = Source("/private.sock", backbone_data_dir=Path(directory))
            source.refresh()
            sessions, error = source.snapshot()
            self.assertEqual(set(sessions), {"ordinary shell", "worker", "parked", "bad-state"})
            self.assertEqual(sessions["worker"]["state"], "busy")
            self.assertEqual(sessions["worker"]["origin"], "backbone")
            self.assertEqual(sessions["ordinary shell"]["origin"], "tmux")
            self.assertTrue(sessions["worker"]["online"])
            self.assertFalse(sessions["parked"]["online"])
            self.assertEqual(sessions["bad-state"]["state"], "unknown")
            self.assertEqual(error, "")
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")
            self.assertTrue(request.full_url.endswith("/api/agents"))
            self.assertEqual(build_opener.call_args.args[0].proxies, {})
            self.assertIsInstance(build_opener.call_args.args[1], NoRedirect)
            source.refresh()
            self.assertEqual(source.snapshot()[0], sessions)
            self.assertIn("states stale", source.snapshot()[1])
            source.refresh()
            self.assertEqual(set(source.snapshot()[0]), {"ordinary shell", "worker"})
            self.assertEqual(source.snapshot()[1], "")

    def test_malformed_api_payload_does_not_replace_cached_roster(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("source.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch("source.urllib.request.build_opener") as build_opener,
        ):
            source = Source("/private.sock", backbone_data_dir=Path(directory))
            for payload in ({"items": {"wrong": "shape"}}, {}, [], None):
                source.roster = {"parked": {"name": "parked", "state": "offline"}}
                build_opener.return_value.open.return_value = io.BytesIO(
                    json.dumps(payload).encode()
                )
                source.refresh()
                self.assertIn("parked", source.snapshot()[0])
                self.assertIn("states stale", source.snapshot()[1])

    def test_malformed_http_transport_keeps_generic_sessions_and_cached_roster(self):
        process = subprocess.CompletedProcess([], 0, stdout="ordinary shell\n", stderr="")
        truncated_response = Mock()
        truncated_response.read.side_effect = http.client.IncompleteRead(b'{"items":', 12)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("source.subprocess.run", return_value=process),
            patch("source.urllib.request.build_opener") as build_opener,
        ):
            source = Source("/private.sock", backbone_data_dir=Path(directory))
            opener = build_opener.return_value
            opener.open.side_effect = [
                io.BytesIO(b'{"items": [{"name": "parked", "state": "offline"}]}'),
                http.client.BadStatusLine("invalid HTTP response"),
                contextlib.nullcontext(truncated_response),
            ]
            source.refresh()
            sessions, error = source.snapshot()
            self.assertEqual(set(sessions), {"ordinary shell", "parked"})
            self.assertEqual(error, "")
            for _ in range(2):
                source.refresh()
                self.assertEqual(source.snapshot()[0], sessions)
                self.assertIn("Backbone unavailable; states stale", source.snapshot()[1])

    def test_local_only_and_safe_targets(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            connection(Path(directory), "https://remote.example")
        for name in ("=agent", "agent:1", "%5", "a; echo hello", "a\x1b[2J"):
            self.assertFalse(valid_agent(name))
        self.assertTrue(valid_agent("swarm-worker_1.test"))
        self.assertFalse(valid_agent(None))
        self.assertFalse(valid_agent(["worker"]))

    def test_exact_session_targets_preserve_printable_names(self):
        for name in ("ordinary shell", "café 東京", "=agent", "%5", "-option", "semi;colon", " a "):
            self.assertTrue(valid_session(name), name)
            self.assertEqual(session_target(name), "=" + name + ":")
        for name in ("", None, ["name"], "name:window", "line\nbreak", "\t", "a\x1b[2J", "a\x7f"):
            self.assertFalse(valid_session(name), repr(name))
            with self.assertRaises(ValueError):
                session_target(name)

    def test_adapter_rejects_redirects_and_nonlocal_or_credential_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            for url in (
                "https://127.0.0.1",
                "http://remote.example",
                "http://user:secret@localhost",
                "http://localhost?secret=value",
                "http://localhost#fragment",
            ):
                with self.assertRaises(ValueError):
                    connection(Path(directory), url)
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, None, None, None, None)

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_real_tmux_exact_targets_and_generic_discovery(self):
        with tempfile.TemporaryDirectory(prefix="tw-src-", dir="/tmp") as directory:
            socket = str(Path(directory) / "s")
            names = ("ordinary shell", "ordinary shell extra", "=leading", "%5", "semi;colon")

            def tmux(*args):
                return subprocess.run(
                    ["tmux", "-S", socket, *args],
                    env=clean_env(),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

            try:
                for name in names:
                    result = tmux("-f", "/dev/null", "new-session", "-d", "-s", name, "/bin/sh")
                    self.assertEqual(result.returncode, 0, result.stderr)
                source = Source(socket)
                source.refresh()
                self.assertEqual(set(source.snapshot()[0]), set(names))
                for name in names:
                    result = tmux(
                        "display-message", "-p", "-t", session_target(name), "#{session_name}"
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.rstrip("\n"), name)
                self.assertNotEqual(
                    tmux("has-session", "-t", session_target("ordinary")).returncode, 0
                )
            finally:
                tmux("kill-server")

    def test_separate_sockets_and_secret_free_child_environment(self):
        with self.assertRaises(ValueError):
            Display(
                "/tmp/same.sock", "/tmp/same.sock", "%0", "/tmp/shell.sock", "/tmp/actions.sock"
            )
        with patch.dict(
            os.environ,
            {
                "BACKBONE_API_KEY": "secret",
                "TMUX": "original",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "PATH": "/bin",
            },
        ):
            env = clean_env()
            self.assertEqual(env["PATH"], "/bin")
            for key in ("BACKBONE_API_KEY", "TMUX", "AWS_SECRET_ACCESS_KEY"):
                self.assertNotIn(key, env)

    def test_preserves_terminal_definitions_for_ghostty(self):
        with patch.dict(
            os.environ,
            {
                "TERM": "xterm-ghostty",
                "TERMINFO": "/terminal/definitions",
                "TERMINFO_DIRS": "/terminal/definitions:/usr/share/terminfo",
            },
        ):
            env = clean_env()
            self.assertEqual(env["TERM"], "xterm-ghostty")
            self.assertEqual(env["TERMINFO"], "/terminal/definitions")
            self.assertEqual(env["TERMINFO_DIRS"], os.environ["TERMINFO_DIRS"])


if __name__ == "__main__":
    unittest.main()
