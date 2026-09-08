"""Accounting and percentile contracts for the opt-in benchmark."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts import benchmark


class BenchmarkTests(unittest.TestCase):
    def test_nearest_rank_tail_and_count(self):
        result = benchmark.summary(list(range(1, 21)))
        self.assertEqual(result["n"], 20)
        self.assertEqual(result["p50_ms"], 10.5)
        self.assertEqual(result["p95_ms"], 19)
        self.assertEqual(benchmark.summary([]), {"n": 0})
        self.assertEqual(benchmark.summary([42])["p95_ms"], 42)

    def test_bsd_cpu_clock_days_hours_and_fraction(self):
        self.assertEqual(benchmark.clock_seconds("02:03.45"), 123.45)
        self.assertEqual(benchmark.clock_seconds("01:02:03"), 3723)
        self.assertEqual(benchmark.clock_seconds("2-01:02:03"), 176523)

    def test_linux_stat_accepts_spaces_and_parentheses_in_process_name(self):
        fields = ["S", "42"] + ["0"] * 9 + ["10", "20", "30", "40"] + ["0"] * 6
        value = "99 (shell (with spaces)) " + " ".join(fields)
        self.assertEqual(benchmark.linux_stat(value, 100), (42, 1.0))

    def test_trace_command_skips_global_options(self):
        self.assertEqual(
            benchmark.traced_command("-S /tmp/test -f /dev/null new-session -d"), "new-session"
        )
        self.assertEqual(
            benchmark.traced_command("-S /tmp/test list-panes -F example"), "list-panes"
        )

    def test_overlapping_roots_do_not_count_descendants_twice(self):
        start = {1: (0, 2), 2: (1, 3), 3: (2, 4), 8: (0, 100)}
        end = {1: (0, 2.5), 2: (1, 3.5), 3: (2, 4.5), 8: (0, 999)}
        result = benchmark.cpu_delta(start, end, [1, 2])
        self.assertEqual(result["cpu_seconds"], 1.5)
        self.assertEqual(result["process_count"], 3)
        self.assertTrue(result["accounting_valid"])

    def test_reaped_transient_child_cpu_is_in_parent(self):
        start = {1: (0, 2), 2: (1, 3)}
        end = {1: (0, 2), 2: (1, 3.7)}
        self.assertAlmostEqual(benchmark.cpu_delta(start, end, [1])["cpu_seconds"], 0.7)

    def test_readiness_requires_clients_from_this_viewers_pane_ttys(self):
        fixture = benchmark.Fixture.__new__(benchmark.Fixture)
        fixture.views, fixture.shell = ["display"], "shells"
        fixture.leaves = lambda tab: [{"id": "abcdef123456"}]
        rows = {
            "display": "||/dev/ttys0\ntab|abcdef123456|/dev/ttys1",
            "shells": "terminal-abcdef123456|/dev/other-viewer",
        }
        fixture.tmux = lambda socket, *args: rows[socket]
        self.assertFalse(fixture.ready({"id": "tab"}))
        rows["shells"] = "terminal-abcdef123456|/dev/ttys1"
        self.assertTrue(fixture.ready({"id": "tab"}))

    def test_provenance_marks_dirty_tracked_source_without_relabeling_head(self):
        with tempfile.TemporaryDirectory(prefix="tw-provenance-") as temporary:
            root = Path(temporary)

            def git(*args):
                subprocess.run(
                    ["git", *args],
                    cwd=root,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )

            git("init", "-q")
            source = root / "runtime.py"
            source.write_text("original\n")
            git("add", "runtime.py")
            git(
                "-c",
                "user.name=Benchmark fixture",
                "-c",
                "user.email=benchmark@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            )
            clean = benchmark.repository_provenance(root)
            self.assertFalse(clean["tracked_dirty"])
            self.assertIsNone(clean["tracked_diff_sha256"])
            source.write_text("changed\n")
            dirty = benchmark.repository_provenance(root)
            self.assertEqual(dirty["revision"], clean["revision"])
            self.assertTrue(dirty["tracked_dirty"])
            self.assertIn("runtime.py", dirty["tracked_changes"][0])
            self.assertIsNotNone(dirty["tracked_diff_sha256"])
            git("add", "runtime.py")
            self.assertEqual(
                benchmark.repository_provenance(root)["tracked_diff_sha256"],
                dirty["tracked_diff_sha256"],
            )

    def test_routing_check_rejects_duplicate_delivery_even_when_target_received_it(self):
        panes = {"target": "BENCH_token", "other": "BENCH_token", "third": "ordinary prompt"}
        with self.assertRaisesRegex(RuntimeError, "another fixture shell"):
            benchmark.assert_unique_ack(panes.__getitem__, "target", "BENCH_token", panes)
        panes["other"] = "ordinary prompt"
        benchmark.assert_unique_ack(panes.__getitem__, "target", "BENCH_token", panes)

    def test_terminal_cleanup_closes_pty_even_when_process_termination_fails(self):
        reader, writer = os.pipe()
        self.addCleanup(os.close, writer)
        client = benchmark.Terminal.__new__(benchmark.Terminal)
        client.master = reader
        client.process = Mock()
        client.process.poll.return_value = None
        client.process.terminate.side_effect = OSError("injected terminate failure")
        with self.assertRaises(ExceptionGroup):
            client.close()
        self.assertIsNone(client.master)
        with self.assertRaises(OSError):
            os.fstat(reader)

    def test_cleanup_attempts_every_client_server_and_lock_after_failures(self):
        with tempfile.TemporaryDirectory(prefix="tw-cleanup-") as temporary:
            fixture = benchmark.Fixture.__new__(benchmark.Fixture)
            fixture.clients = [Mock(), Mock()]
            fixture.clients[0].close.side_effect = RuntimeError("first client failed")
            fixture.views = [str(Path(temporary) / "one.sock"), str(Path(temporary) / "two.sock")]
            fixture.shell = str(Path(temporary) / "shells.sock")
            fixture.source = Path(temporary) / "source.sock"
            sockets = [*fixture.views, fixture.shell, str(fixture.source)]
            for socket in sockets:
                Path(socket + ".viewer-lock").touch()
            fixture.tmux = Mock(side_effect=[RuntimeError("first server failed"), "", "", ""])
            with self.assertRaises(ExceptionGroup) as caught:
                fixture.close()
            self.assertEqual(len(caught.exception.exceptions), 2)
            for client in fixture.clients:
                client.close.assert_called_once()
            self.assertEqual([call.args[0] for call in fixture.tmux.call_args_list], sockets)
            self.assertFalse(any(Path(socket + ".viewer-lock").exists() for socket in sockets))

    def test_churn_missing_roots_or_counter_regression_invalidate_cpu(self):
        start = {1: (0, 2), 2: (1, 3)}
        for end in ({1: (0, 6)}, {2: (0, 4)}, {1: (0, 1), 2: (1, 3)}):
            result = benchmark.cpu_delta(start, end, [1])
            self.assertIsNone(result["cpu_seconds"])
            self.assertFalse(result["accounting_valid"])


if __name__ == "__main__":
    unittest.main()
