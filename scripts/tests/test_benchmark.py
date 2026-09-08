"""Accounting and percentile contracts for the opt-in benchmark."""

import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location(
    "benchmark", Path(__file__).resolve().parents[1] / "benchmark.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


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
        fixture.leaves = lambda tab: [{"id": "leaf"}]
        fixture.terminal_module = SimpleNamespace(
            Shells=SimpleNamespace(name=lambda pane: "terminal-leaf")
        )
        rows = {
            "display": "||/dev/ttys0\ntab|leaf|/dev/ttys1",
            "shells": "terminal-leaf|/dev/other-viewer",
        }
        fixture.tmux = lambda socket, *args: rows[socket]
        self.assertFalse(fixture.ready({"id": "tab"}))
        rows["shells"] = "terminal-leaf|/dev/ttys1"
        self.assertTrue(fixture.ready({"id": "tab"}))

    def test_churn_missing_roots_or_counter_regression_invalidate_cpu(self):
        start = {1: (0, 2), 2: (1, 3)}
        for end in ({1: (0, 6)}, {2: (0, 4)}, {1: (0, 1), 2: (1, 3)}):
            result = benchmark.cpu_delta(start, end, [1])
            self.assertIsNone(result["cpu_seconds"])
            self.assertFalse(result["accounting_valid"])


if __name__ == "__main__":
    unittest.main()
