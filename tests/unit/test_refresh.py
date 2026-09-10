"""Refreshing a viewer replaces only its own window, in a fresh interpreter."""

import contextlib
import io
import json
import os
import shlex
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces import application, cli, relaunch, supervisor
from tmux_workspaces.cli import parser


class Child:
    """One recorded window, reporting whatever the test asked it to report."""

    def __init__(self, argv, code=0, status=None):
        self.argv, self.code, self.status = argv, code, status
        self.signals = []
        self.waited = False
        self.killed = False
        self.pid = 4242

    def option(self, name):
        return self.argv[self.argv.index(name) + 1] if name in self.argv else None

    def send_signal(self, number):
        self.signals.append(number)

    def wait(self, timeout=None):
        self.waited = True
        return self.code


class SupervisorFixture:
    """Run supervise() without starting interpreters, recording each window."""

    def __init__(self, reports):
        self.reports = list(reports)
        self.children: list[Child] = []
        self.recovered: list[tuple[str, str]] = []
        self.live = 0

    def spawn(self, command, **_kwargs):
        assert self.live == 0, "a second window started before the first had finished"
        argv = list(command)
        code, status = self.reports.pop(0) if self.reports else (0, {"ready": True})
        child = Child(argv, code, status)
        handover = child.option("--handover")
        if status is not None and handover:
            Path(handover).write_text(json.dumps(status))
        self.children.append(child)
        self.live += 1
        original = child.wait

        def wait(timeout=None):
            self.live -= 1
            if child.code == "signal":
                # Something stopped this launcher while it owned a window.
                child.code = 0
                signal.raise_signal(signal.SIGTERM)
            return original(timeout)

        child.wait = wait
        return child

    def recover(self, reason, command):
        self.recovered.append((reason, command))
        return 1

    @contextlib.contextmanager
    def patched(self):
        with (
            patch.object(supervisor.subprocess, "Popen", side_effect=self.spawn),
            patch.object(supervisor.relaunch, "recover", side_effect=self.recover),
        ):
            yield self


class SupervisorTests(unittest.TestCase):
    @contextlib.contextmanager
    def supervised(self, arguments, reports):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SupervisorFixture(reports)
            args = parser().parse_args([*arguments, "--data-dir", str(Path(directory) / "library")])
            with fixture.patched():
                yield fixture, args

    def test_one_request_opens_one_replacement_in_a_new_window(self):
        selection = {"workspace": "w1", "tab": "t1", "leaf": "l1", "focus": False}
        reports = [
            (0, {"ready": True, "relaunch": True, "navigation": selection}),
            (0, {"ready": True, "relaunch": False}),
        ]
        with self.supervised(["--no-keymap"], reports) as (fixture, args):
            self.assertEqual(supervisor.supervise(args), 0)
        self.assertEqual(len(fixture.children), 2)
        first, second = fixture.children
        self.assertIn("_window", first.argv)
        self.assertNotEqual(first.option("--handover"), second.option("--handover"))
        self.assertIsNone(first.option("--carry-navigation"))
        self.assertEqual(json.loads(second.option("--carry-navigation")), selection)
        for child in fixture.children:
            # Display resources belong to a window, never to its successor.
            for private in ("--viewer-socket", "--instance-dir", "--action-socket"):
                self.assertNotIn(private, child.argv)
            self.assertEqual(child.option("--supervisor-pid"), str(os.getpid()))
            self.assertTrue(child.waited)
        self.assertEqual(fixture.recovered, [])
        self.assertFalse(Path(second.option("--handover")).exists())

    def test_no_request_runs_exactly_one_window(self):
        with self.supervised(["--no-keymap"], [(0, {"ready": True, "relaunch": False})]) as (
            fixture,
            args,
        ):
            self.assertEqual(supervisor.supervise(args), 0)
        self.assertEqual(len(fixture.children), 1)

    def test_repeated_refreshes_keep_one_supervisor_and_one_window_at_a_time(self):
        asked = {"ready": True, "relaunch": True}
        reports = [(0, asked), (0, asked), (0, asked), (0, {"ready": True, "relaunch": False})]
        with self.supervised(["--no-keymap"], reports) as (fixture, args):
            self.assertEqual(supervisor.supervise(args), 0)
        # Four windows in sequence, and the fixture fails if two ever overlap.
        self.assertEqual(len(fixture.children), 4)
        self.assertEqual(fixture.recovered, [])

    def test_a_first_window_that_fails_is_left_to_explain_itself(self):
        for report in ((1, None), (0, None), (0, {"ready": False, "relaunch": True})):
            with (
                self.subTest(report=report),
                self.supervised(["--no-keymap"], [report]) as (fixture, args),
            ):
                self.assertEqual(supervisor.supervise(args), report[0] or 1)
                self.assertEqual(len(fixture.children), 1)
                self.assertEqual(fixture.recovered, [])

    def test_a_failed_replacement_hands_back_a_usable_terminal(self):
        asked = {"ready": True, "relaunch": True}
        for report, expected in (
            ((2, None), "exited with status 2"),
            ((0, None), "closed before it finished starting"),
            ((0, {"ready": False, "relaunch": False}), "closed before it finished starting"),
        ):
            with (
                self.subTest(report=report),
                self.supervised(["--no-keymap"], [(0, asked), report]) as (fixture, args),
            ):
                self.assertEqual(supervisor.supervise(args), 1)
                self.assertEqual(len(fixture.children), 2)
                self.assertEqual(len(fixture.recovered), 1)
                reason, command = fixture.recovered[0]
                self.assertIn(expected, reason)
                self.assertIn("--data-dir", command)
                self.assertNotIn("_window", command)

    def test_a_signal_reaches_the_window_and_waits_for_its_cleanup(self):
        child = Child([], code=0)
        cleanups = []

        def wait(timeout=None):
            first = not cleanups
            cleanups.append(timeout)
            if first:
                signal.raise_signal(signal.SIGTERM)
            return 0

        child.wait = wait
        before = signal.getsignal(signal.SIGTERM)
        with supervisor.Windows() as windows:
            windows.adopt(child)
            self.assertEqual(windows.wait(), (128 + signal.SIGTERM, True))
        self.assertEqual(child.signals, [signal.SIGTERM])
        # The window is given a bounded grace to finish its own cleanup.
        self.assertTrue(0 < cleanups[-1] <= supervisor.STOP_GRACE, cleanups)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_a_signal_during_a_start_still_reaches_that_window(self):
        child = Child([], code=0)
        with supervisor.Windows() as windows:
            # The window exists, but this launcher has not adopted it yet.
            windows._handle(signal.SIGTERM, None)
            self.assertEqual(child.signals, [])
            windows.adopt(child)
            self.assertEqual(child.signals, [signal.SIGTERM])
            self.assertEqual(windows.wait(), (128 + signal.SIGTERM, True))
        # The terminal delivers to the whole group as well, and a second copy
        # can interrupt a window that is already cleaning up.
        self.assertEqual(child.signals, [signal.SIGTERM])

    def test_a_signal_before_a_window_is_adopted_still_bounds_its_stop(self):
        child = Child([], code=0)
        child.kill = lambda: setattr(child, "killed", True)

        def wait(timeout=None):
            assert timeout is not None, "waited without limit for a window already asked to stop"
            if child.killed:
                return -signal.SIGKILL
            raise supervisor.subprocess.TimeoutExpired("window", timeout)

        child.wait = wait
        with (
            contextlib.redirect_stderr(io.StringIO()),
            supervisor.Windows(grace=0.05) as windows,
        ):
            windows._handle(signal.SIGTERM, None)
            windows.adopt(child)
            self.assertEqual(windows.wait(), (128 + signal.SIGTERM, True))
        self.assertTrue(child.killed)

    def test_repeated_signals_do_not_buy_a_stopping_window_more_time(self):
        windows = supervisor.Windows(grace=5.0)
        first = windows._deadline(None)
        self.assertEqual(windows._deadline(first), first)
        self.assertEqual(windows._deadline(first), first)

    def test_a_window_that_ignores_its_stop_is_ended_and_said_so(self):
        errors = io.StringIO()
        child = Child([], code=0)

        def wait(timeout=None):
            if child.killed:
                return -signal.SIGKILL
            raise supervisor.subprocess.TimeoutExpired("window", timeout or 0)

        child.wait = wait
        child.kill = lambda: setattr(child, "killed", True)
        with (
            contextlib.redirect_stderr(errors),
            supervisor.Windows(grace=0.05) as windows,
        ):
            windows.adopt(child)
            windows._handle(signal.SIGTERM, None)
            self.assertEqual(windows.wait(), (128 + signal.SIGTERM, True))
        self.assertTrue(child.killed, "an ignoring window was left holding the terminal")
        self.assertIn("did not stop within", errors.getvalue())
        self.assertIn("cleanup may be incomplete", errors.getvalue())

    def test_stopping_the_launcher_is_not_a_failed_replacement(self):
        asked = {"ready": True, "relaunch": True}
        reports = [(0, asked), ("signal", None)]
        with self.supervised(["--no-keymap"], reports) as (fixture, args):
            self.assertEqual(supervisor.supervise(args), 128 + signal.SIGTERM)
        self.assertEqual(fixture.children[-1].signals, [signal.SIGTERM])
        self.assertEqual(fixture.recovered, [], "a stopped launcher offered a recovery shell")


class StopSignalTests(unittest.TestCase):
    def test_one_stop_ignores_every_signal_that_could_cut_its_cleanup_short(self):
        installed = {}
        with patch.object(cli.signal, "signal", side_effect=installed.__setitem__):
            cli.install_stop_signals()
        self.assertEqual(set(installed), set(cli.STOP_SIGNALS))
        stop = installed[signal.SIGTERM]
        self.assertEqual(len(set(installed.values())), 1)
        with (
            patch.object(cli.signal, "signal", side_effect=installed.__setitem__),
            self.assertRaises(SystemExit) as stopped,
        ):
            stop(signal.SIGTERM, None)
        self.assertEqual(stopped.exception.code, 128 + signal.SIGTERM)
        # A second copy, or a different kind arriving behind it, changes nothing.
        self.assertEqual(
            installed,
            dict.fromkeys(cli.STOP_SIGNALS, signal.SIG_IGN),
            "cleanup stays interruptible",
        )


class LaunchContextTests(unittest.TestCase):
    def context(self, arguments):
        return supervisor.LaunchContext(parser().parse_args(arguments))

    def test_a_disposable_library_is_resolved_once_and_asked_for_by_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.context(["--demo", "--no-keymap", "--data-dir", str(root)])
            self.assertEqual(context.library, root / "demo")
            handover = root / "handover.json"
            child = context.child_args(handover, None)
            self.assertEqual(child[child.index("--data-dir") + 1], str(root / "demo"))
            # A reopened command must not nest another disposable directory.
            self.assertEqual(shlex.split(context.reopen)[3], str(root))
            self.assertIn("--demo", shlex.split(context.reopen))
            self.assertNotIn("--source-socket", context.reopen)

    def test_the_reopen_command_names_the_library_and_settings_it_was_given(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "keys.toml").write_text('prefix = "C-a"\n')
            context = self.context(
                [
                    "--data-dir",
                    str(root / "lib rary"),
                    "--source-socket",
                    str(root / "outer.sock"),
                    "--keymap",
                    str(root / "keys.toml"),
                ]
            )
            self.assertEqual(
                shlex.split(context.reopen)[2:],
                [
                    "--data-dir",
                    str(root / "lib rary"),
                    "--theme",
                    str(context.theme),
                    "--source-socket",
                    str(root / "outer.sock"),
                    "--keymap",
                    str(root / "keys.toml"),
                ],
            )
            self.assertEqual(context.published, context.reopen)
            child = context.child_args(root / "handover.json", None)
            self.assertEqual(child[child.index("--keymap-source") + 1], str(root / "keys.toml"))
            self.assertIn("--keymap-required", child)

    def test_an_optional_default_keymap_stays_optional_for_the_next_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root)}):
                context = self.context(["--data-dir", str(root)])
            child = context.child_args(root / "handover.json", None)
            self.assertIn("--keymap-source", child)
            self.assertNotIn("--keymap-required", child)
            self.assertNotIn("--keymap", shlex.split(context.reopen))

    def test_a_window_with_a_fixed_map_is_told_to_be_reopened_by_hand(self):
        pinned = 'prefix = "C-b"\n\n[bindings]\n\n[direct]\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context = self.context(["--data-dir", str(root), "--keymap-state", pinned])
            child = context.child_args(root / "handover.json", None)
        self.assertIn("--manual-reopen", child)
        self.assertEqual(child[child.index("--keymap-state") + 1], pinned)
        # Without a dedicated profile the ordinary command still reopens it.
        self.assertEqual(context.published, context.reopen)

    def test_a_fixed_map_window_still_knows_which_file_its_keys_came_from(self):
        """Frozen keys are edited in the file that produced them, not nowhere.

        A Ghostty window runs the snapshot its launcher took, but the shortcut
        editor writes a file. Dropping the selection here left every launched
        window unable to edit shortcuts at all.
        """
        pinned = 'prefix = "C-b"\n\n[bindings]\n\n[direct]\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            keys = root / "keys.toml"
            context = self.context(
                ["--data-dir", str(root), "--keymap-state", pinned, "--keymap", str(keys)]
            )
            child = context.child_args(root / "handover.json", None)
            self.assertEqual(child[child.index("--keymap-source") + 1], str(keys))
            self.assertIn("--keymap-required", child)
            self.assertIn("--manual-reopen", child)
            # The optional default is named too, so a first save can create it.
            with patch.dict(os.environ, {"HOME": str(root), "XDG_CONFIG_HOME": str(root)}):
                context = self.context(["--data-dir", str(root), "--keymap-state", pinned])
            child = context.child_args(root / "handover.json", None)
            self.assertEqual(
                child[child.index("--keymap-source") + 1],
                str(root / "tmux-workspaces" / "keymap.toml"),
            )
            self.assertNotIn("--keymap-required", child)
            # Frozen keys never make the file mandatory for the window itself.
            self.assertNotIn("--no-keymap", child)


class WindowTests(unittest.TestCase):
    """One supervised window: its own display, its own report."""

    @contextlib.contextmanager
    def window(self, arguments, *, attach=0, started=True, no_keymap=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handover = root / "handover.json"
            args = parser().parse_args(
                [
                    "_window",
                    *(["--no-keymap"] if no_keymap else []),
                    "--data-dir",
                    str(root / "library"),
                    "--handover",
                    str(handover),
                    "--supervisor-pid",
                    "4242",
                    *arguments,
                ]
            )
            recorded = {}

            def start(command, **_kwargs):
                argv = shlex.split(command[-1])
                recorded["child"] = argv[argv.index("_sidebar") :]
                instance = Path(argv[argv.index("--instance-dir") + 1])
                recorded["instance"] = instance
                recorded["manifest"] = json.loads((instance / "runtime.json").read_text())
                if started:
                    relaunch.started(instance)
                return Mock(returncode=0)

            with (
                patch.object(application, "check_startup"),
                patch.object(application, "Store"),
                patch.object(
                    application, "socket_path", side_effect=lambda _l, name: str(root / name)
                ),
                patch.object(application, "Tmux", return_value=Mock()),
                patch.object(application.subprocess, "run", side_effect=start),
                patch.object(application.subprocess, "call", return_value=attach),
            ):
                yield args, handover, recorded

    def test_a_clean_window_reports_after_its_own_cleanup(self):
        selection = {"workspace": "w1", "tab": "t1", "leaf": "l1", "focus": True}
        with self.window([]) as (args, handover, recorded):
            marker = None

            def request(instance_dir):
                nonlocal marker
                marker = Path(instance_dir).exists()
                return {"navigation": selection}

            with patch.object(application.relaunch, "take_request", side_effect=request):
                self.assertEqual(application.window_main(args), 0)
            self.assertTrue(marker, "the request was read after its window was removed")
            self.assertFalse(recorded["instance"].exists())
            self.assertEqual(
                relaunch.read_status(handover),
                {"ready": True, "relaunch": True, "navigation": selection},
            )

    def test_a_quiet_exit_reports_no_replacement(self):
        with self.window([]) as (args, handover, _recorded):
            self.assertEqual(application.window_main(args), 0)
            self.assertEqual(
                relaunch.read_status(handover),
                {"ready": True, "relaunch": False, "navigation": None},
            )

    def test_a_window_that_never_came_up_says_so(self):
        with self.window([], started=False) as (args, handover, _recorded):
            self.assertEqual(application.window_main(args), 0)
            self.assertFalse(relaunch.read_status(handover)["ready"])

    def test_a_failing_window_reports_nothing_at_all(self):
        with self.window([]) as (args, handover, _recorded):
            with (
                patch.object(application, "check_startup", side_effect=RuntimeError("no tmux")),
                self.assertRaisesRegex(RuntimeError, "no tmux"),
            ):
                application.window_main(args)
            self.assertIsNone(relaunch.read_status(handover))

    def test_the_manifest_names_the_launcher_and_its_current_window(self):
        with self.window([]) as (args, _handover, recorded):
            self.assertEqual(application.window_main(args), 0)
        self.assertEqual(recorded["manifest"]["pid"], 4242)
        self.assertEqual(recorded["manifest"]["window_pid"], os.getpid())

    def test_a_fixed_map_window_never_asks_for_a_replacement(self):
        pinned = 'prefix = "C-b"\n\n[bindings]\n\n[direct]\n'
        with self.window(["--manual-reopen", "--keymap-state", pinned]) as (
            args,
            handover,
            recorded,
        ):
            with patch.object(
                application.relaunch,
                "take_request",
                side_effect=AssertionError("consulted a fixed window's request"),
            ):
                self.assertEqual(application.window_main(args), 0)
            self.assertFalse(relaunch.read_status(handover)["relaunch"])
        self.assertIn("--manual-reopen", recorded["child"])

    def test_a_fixed_map_window_hands_its_keymap_file_to_the_sidebar(self):
        """Manual reopen disables refresh, not editing: the file still travels."""
        pinned = 'prefix = "C-b"\n\n[bindings]\n\n[direct]\n'
        with self.window(
            ["--manual-reopen", "--keymap-state", pinned, "--keymap-source", "/cfg/keys.toml"],
            no_keymap=False,
        ) as (args, _handover, recorded):
            self.assertEqual(application.window_main(args), 0)
        child = recorded["child"]
        self.assertIn("--manual-reopen", child)
        self.assertEqual(child[child.index("--keymap-source") + 1], "/cfg/keys.toml")
        # The sidebar runs the snapshot and edits the file; both survive parsing.
        sidebar = parser().parse_args(child)
        self.assertEqual(sidebar.keymap_source, Path("/cfg/keys.toml"))
        self.assertEqual(cli.effective_keymap(sidebar).prefix, "C-b")

    def test_a_window_without_keymaps_offers_the_sidebar_nothing_to_edit(self):
        """--no-keymap stays authoritative even if a source is named beside it."""
        with self.window(["--keymap-source", "/cfg/keys.toml"]) as (args, _handover, recorded):
            self.assertEqual(application.window_main(args), 0)
        child = recorded["child"]
        self.assertNotIn("--keymap-source", child)
        self.assertNotIn("--keymap-required", child)
        sidebar = parser().parse_args(child)
        self.assertIsNone(sidebar.keymap or sidebar.keymap_source)

    def test_the_window_hands_its_navigation_and_reopen_text_to_the_sidebar(self):
        selection = json.dumps({"workspace": "w1", "tab": "", "leaf": "", "focus": False})
        with self.window(
            ["--carry-navigation", selection, "--reopen-command", "run --data-dir '/lib rary'"]
        ) as (args, _handover, recorded):
            self.assertEqual(application.window_main(args), 0)
        child = recorded["child"]
        self.assertEqual(child[child.index("--carry-navigation") + 1], selection)
        self.assertEqual(child[child.index("--reopen-command") + 1], "run --data-dir '/lib rary'")


class SidebarWiringTests(unittest.TestCase):
    def test_a_viewer_restores_its_own_navigation_and_owns_one_request(self):
        with tempfile.TemporaryDirectory() as directory:
            selection = {"workspace": "w1", "tab": "", "leaf": "", "focus": False}
            args = parser().parse_args(
                [
                    "_sidebar",
                    "--no-keymap",
                    "--data-dir",
                    directory,
                    "--carry-navigation",
                    json.dumps(selection),
                    "--reopen-command",
                    "run --data-dir /library",
                ]
            )
            args.instance_dir = Path(directory)
            args.viewer_socket = "/fixture/private-view.sock"
            args.source_socket = "/fixture/external.sock"
            args.shell_socket = "/fixture/shared-shells.sock"
            args.action_socket = "/fixture/actions.sock"
            args.keymap_source = Path("/fixture/keymap.toml")
            args.keymap_required = True
            args.shortcut_hints = "command"
            args.host_socket, args.host_pane = "/fixture/host.sock", "%3"
            store, model = Mock(), Mock(state={"selected": "w1"})
            store.load.return_value = model
            curses = Mock()
            curses.error = type("CursesError", (Exception,), {})
            tmux = Mock()
            tmux.run.return_value = "1"
            request = Mock()
            sidebar = Mock()
            with (
                patch.object(application, "Tmux", return_value=tmux),
                patch.object(application, "load_curses", return_value=curses),
                patch.object(application, "make_source", return_value=Mock()),
                patch.object(application, "Store", return_value=store),
                patch.object(application, "Actions"),
                patch.object(application, "Display"),
                patch.object(application.relaunch, "Request", return_value=request) as requests,
                patch.object(application.relaunch, "started") as ready,
                patch("tmux_workspaces.sidebar.Sidebar", sidebar),
                patch.dict("os.environ", {"TMUX_PANE": "%0"}),
            ):
                self.assertEqual(application.sidebar_main(args), 0)
            self.assertEqual(model.state["selected"], "w1")
            ready.assert_called_once_with(args.instance_dir)
            requests.assert_called_once_with(
                args.instance_dir,
                keymap_source=args.keymap_source,
                keymap_required=True,
                manual_reopen=False,
                profile_hints=True,
                nested=True,
                reopen_command="run --data-dir /library",
            )
            curses.wrapper.call_args.args[0](Mock())
            self.assertIs(sidebar.call_args.kwargs["relaunch"], request)


if __name__ == "__main__":
    unittest.main()
