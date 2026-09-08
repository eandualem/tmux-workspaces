"""Provider contract: availability, stale metadata, read-only scope and isolation."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces.adapters.backbone import BackboneProvider
from tmux_workspaces.adapters.demo import DemoProvider
from tmux_workspaces.adapters.tmux import TmuxProvider
from tmux_workspaces.discovery import Snapshot
from tmux_workspaces.source import Source


class DiscoveryTests(unittest.TestCase):
    def test_application_default_imports_no_optional_provider(self):
        code = (
            "import sys; from tmux_workspaces.application import make_source; "
            "source = make_source('/unused/source.sock'); "
            "assert 'tmux_workspaces.adapters.backbone' not in sys.modules; "
            "assert 'tmux_workspaces.adapters.demo' not in sys.modules; "
            "assert source.overlays == (); assert source.persistent_socket"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_composition_does_not_require_a_known_integration(self):
        discovery = Mock(
            read=Mock(
                return_value=Snapshot(
                    {"live": {"name": "live", "online": True, "state": "running"}}, observed_at=30
                )
            )
        )
        first = Mock(
            read=Mock(
                return_value=Snapshot(
                    {
                        "live": {"name": "live", "online": False, "state": "busy"},
                        "offline": {"name": "offline", "online": True, "state": "waiting"},
                    },
                    error="metadata unavailable",
                    stale=True,
                    observed_at=10,
                )
            )
        )
        second = Mock(
            read=Mock(
                return_value=Snapshot(
                    {"other": {"name": "other", "online": True, "state": "idle"}}, observed_at=20
                )
            )
        )
        source = Source("/explicit/source.sock", discovery, (first, second))
        source.refresh()
        observation = source.observation()
        self.assertTrue(observation.stale)
        self.assertEqual(observation.observed_at, 10)
        self.assertEqual(observation.error, "metadata unavailable")
        self.assertTrue(observation.sessions["live"]["online"])
        self.assertEqual(observation.sessions["live"]["state"], "busy")
        self.assertFalse(observation.sessions["offline"]["online"])
        self.assertFalse(observation.sessions["other"]["online"])
        self.assertEqual(source.socket, "/explicit/source.sock")
        for provider in (discovery, first, second):
            self.assertEqual(provider.method_calls, [("read", (), {})])

    def test_overlay_cannot_promote_an_explicitly_offline_discovery_record(self):
        discovery = Mock(
            read=Mock(return_value=Snapshot({"offline": {"name": "offline", "online": False}}))
        )
        metadata = Mock(
            read=Mock(
                return_value=Snapshot(
                    {"offline": {"name": "offline", "online": True, "state": "busy"}}
                )
            )
        )
        source = Source("/explicit/source.sock", discovery, (metadata,))
        source.refresh()
        self.assertFalse(source.snapshot()[0]["offline"]["online"])

    def test_raising_overlay_keeps_other_providers_live_and_recovers(self):
        discovery = Mock(
            read=Mock(
                return_value=Snapshot({"live": {"name": "live", "online": True}}, observed_at=30)
            )
        )
        first = Mock(
            read=Mock(
                side_effect=[
                    Snapshot({"cached": {"name": "cached", "state": "busy"}}, observed_at=10),
                    TimeoutError("credential-must-not-escape"),
                    Snapshot({"recovered": {"name": "recovered"}}, observed_at=40),
                ]
            )
        )
        second = Mock(
            read=Mock(
                side_effect=[
                    Snapshot({"other": {"name": "other", "state": "old"}}, observed_at=20),
                    Snapshot({"other": {"name": "other", "state": "new"}}, observed_at=35),
                    Snapshot({"other": {"name": "other", "state": "new"}}, observed_at=45),
                ]
            )
        )
        source = Source("/explicit/source.sock", discovery, (first, second))
        source.refresh()
        source.refresh()
        failed = source.observation()
        self.assertEqual(set(failed.sessions), {"live", "cached", "other"})
        self.assertTrue(failed.sessions["live"]["online"])
        self.assertEqual(failed.sessions["other"]["state"], "new")
        self.assertEqual(failed.sessions["cached"]["state"], "busy")
        self.assertEqual(failed.error, "Session metadata unavailable; states stale")
        self.assertEqual(failed.observed_at, 10)
        self.assertTrue(failed.stale)
        source.refresh()
        recovered = source.observation()
        self.assertEqual(set(recovered.sessions), {"live", "recovered", "other"})
        self.assertEqual(recovered.error, "")
        self.assertFalse(recovered.stale)

    def test_discovery_exception_marks_cached_names_offline_and_still_reads_metadata(self):
        discovery = Mock(
            read=Mock(
                side_effect=[
                    Snapshot({"live": {"name": "live", "online": True}}, observed_at=10),
                    RuntimeError("private failure detail"),
                ]
            )
        )
        metadata = Mock(
            read=Mock(
                return_value=Snapshot(
                    {
                        "live": {"name": "live", "online": True, "state": "busy"},
                        "other": {"name": "other"},
                    },
                    observed_at=20,
                )
            )
        )
        source = Source("/explicit/source.sock", discovery, (metadata,))
        source.refresh()
        source.refresh()
        result = source.observation()
        self.assertEqual(set(result.sessions), {"live", "other"})
        self.assertFalse(result.sessions["live"]["online"])
        self.assertFalse(result.sessions["other"]["online"])
        self.assertEqual(metadata.read.call_count, 2)
        self.assertEqual(result.error, "Session discovery unavailable; states stale")
        self.assertTrue(result.stale)
        self.assertEqual(result.observed_at, 10)

    def test_malformed_provider_snapshot_does_not_break_composition(self):
        primary = Mock(read=Mock(return_value=Snapshot({"live": {"name": "live", "online": True}})))
        malformed = [
            None,
            Snapshot(sessions=[]),
            Snapshot(sessions={"invalid:target": {}}),
            Snapshot(sessions={"item": None}),
            Snapshot(observed_at="not-a-time"),
        ]
        for value in malformed:
            with self.subTest(value=value):
                source = Source(
                    "/explicit/source.sock", primary, (Mock(read=Mock(return_value=value)),)
                )
                source.refresh()
                self.assertTrue(source.snapshot()[0]["live"]["online"])
                self.assertTrue(source.observation().stale)

    def test_error_snapshot_does_not_discard_last_good_fallback(self):
        discovery = Mock(read=Mock(return_value=Snapshot()))
        overlay = Mock(
            read=Mock(
                side_effect=[
                    Snapshot({"cached": {"name": "cached"}}, observed_at=10),
                    Snapshot(error="provider temporarily empty", stale=True),
                    TimeoutError("not for display"),
                ]
            )
        )
        source = Source("/explicit/source.sock", discovery, (overlay,))
        source.refresh()
        source.refresh()
        source.refresh()
        self.assertIn("cached", source.snapshot()[0])
        self.assertTrue(source.observation().stale)

    def test_provider_cancellation_is_not_swallowed(self):
        source = Source("/explicit/source.sock", Mock(read=Mock(side_effect=SystemExit(7))))
        with self.assertRaises(SystemExit):
            source.refresh()

    def test_snapshot_consumers_cannot_mutate_provider_or_shared_cache(self):
        provider = Mock(
            read=Mock(
                return_value=Snapshot(
                    {"live": {"name": "live", "tags": ["build"], "online": True}}, observed_at=10
                )
            )
        )
        source = Source("/explicit/source.sock", provider)
        source.refresh()
        sessions, _ = source.snapshot()
        sessions["live"]["tags"].append("unwanted mutation")
        source.observation().sessions.clear()
        self.assertEqual(source.snapshot()[0]["live"]["tags"], ["build"])
        self.assertEqual(provider.read.return_value.sessions["live"]["tags"], ["build"])

    def test_backbone_timeout_retains_observation_time_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            provider = BackboneProvider(path, "http://127.0.0.1:7120")
            opener = Mock()
            opener.open.side_effect = [
                io.BytesIO(b'{"items": [{"name": "worker", "state": "busy"}]}'),
                TimeoutError("secret or transport detail must not escape"),
                io.BytesIO(b'{"items": []}'),
            ]
            with (
                patch(
                    "tmux_workspaces.adapters.backbone.urllib.request.build_opener",
                    return_value=opener,
                ),
                patch("tmux_workspaces.adapters.backbone.time.time", side_effect=[10, 20]),
            ):
                first = provider.read()
                stale = provider.read()
                recovered = provider.read()
            self.assertEqual(first.observed_at, 10)
            self.assertFalse(first.stale)
            self.assertEqual(stale.observed_at, 10)
            self.assertTrue(stale.stale)
            self.assertEqual(stale.sessions, first.sessions)
            self.assertEqual(stale.error, "Backbone unavailable; states stale")
            self.assertEqual(recovered.observed_at, 20)
            self.assertEqual(recovered.sessions, {})
            self.assertFalse(recovered.stale)
            self.assertEqual(recovered.error, "")
            self.assertEqual(list(path.iterdir()), [])
            for call in opener.open.call_args_list:
                self.assertEqual(call.args[0].get_method(), "GET")
                self.assertEqual(call.kwargs["timeout"], 3)

    def test_demo_failure_and_recovery_preserve_only_last_successful_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.json"
            provider = DemoProvider(path)
            missing = provider.read()
            self.assertTrue(missing.stale)
            self.assertIsNone(missing.observed_at)
            path.write_text(json.dumps([{"name": "shell with spaces", "online": True}]))
            before = path.read_bytes()
            first = provider.read()
            self.assertEqual(path.read_bytes(), before)
            path.write_text('{"wrong": "shape"}')
            failure = provider.read()
            self.assertEqual(failure.sessions, first.sessions)
            self.assertEqual(failure.observed_at, first.observed_at)
            path.write_text("[]")
            self.assertEqual(provider.read().sessions, {})
            self.assertFalse(provider.read().stale)

    def test_failed_tmux_query_never_claims_previous_sessions_still_online(self):
        provider = TmuxProvider("/absent/source.sock")
        with patch("tmux_workspaces.adapters.tmux.subprocess.run") as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, " a \n", ""), TimeoutError()]
            online = provider.read()
            failed = provider.read()
        self.assertTrue(online.sessions[" a "]["online"])
        self.assertTrue(failed.stale)
        self.assertEqual(failed.sessions, {})
        self.assertIsNone(failed.observed_at)
        for call in run.call_args_list:
            self.assertEqual(
                call.args[0],
                ["tmux", "-S", "/absent/source.sock", "list-sessions", "-F", "#{session_name}"],
            )
            self.assertNotIn("BACKBONE_API_KEY", call.kwargs["env"])

    def test_adapter_initialization_does_not_read_credentials_or_create_files(self):
        with tempfile.TemporaryDirectory() as directory:
            absent = Path(directory) / "not-created"
            with (
                patch.object(Path, "read_text", side_effect=AssertionError("eager config read")),
                patch.dict(os.environ, {"BACKBONE_API_KEY": "not-an-observation"}),
            ):
                provider = BackboneProvider(absent)
            self.assertEqual(provider.last, Snapshot())
            self.assertFalse(absent.exists())
