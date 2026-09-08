"""A refresh request is one small, validated file inside its own window."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces import relaunch
from tmux_workspaces.layout_validation import validate_state
from tmux_workspaces.model import Model, leaves


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="tw-relaunch-"))
        self.addCleanup(self.cleanup)
        self.checkout = self.directory / "checkout"
        self.checkout.mkdir()
        # A launcher stands in for the real one: check() runs it read-only.
        (self.checkout / "run").write_text("import sys\nsys.exit(0)\n")
        self.argv = [sys.executable, str(self.checkout / "run")]
        self.tmux = patch.object(relaunch, "check_tmux")
        self.tmux.start()
        self.addCleanup(self.tmux.stop)
        self.launcher = patch.object(relaunch, "application_argv", lambda *args: self.argv)
        self.launcher.start()
        self.addCleanup(self.launcher.stop)

    def cleanup(self):
        for path in sorted(self.directory.rglob("*"), reverse=True):
            path.chmod(0o700)
            path.rmdir() if path.is_dir() else path.unlink()
        self.directory.rmdir()

    def request(self, **options) -> relaunch.Request:
        return relaunch.Request(self.directory, **options)

    def navigation(self, **overrides) -> dict:
        return {"workspace": "w1", "tab": "t1", "leaf": "l1", "focus": False} | overrides

    def test_one_gesture_records_one_request_that_the_window_consumes_once(self):
        request = self.request()
        self.assertIsNone(request.check())
        self.assertTrue(request.submit(self.navigation()))
        self.assertTrue(request.requested)
        # Duplicate confirmations, and a second reader of the same directory,
        # both find the request already made.
        self.assertFalse(request.submit(self.navigation()))
        self.assertFalse(self.request().submit(self.navigation()))
        taken = relaunch.take_request(self.directory)
        self.assertEqual(taken, {"navigation": self.navigation()})
        self.assertIsNone(relaunch.take_request(self.directory))

    def test_requests_without_navigation_still_replace_the_window(self):
        self.assertTrue(self.request().submit(None))
        self.assertEqual(relaunch.take_request(self.directory), {"navigation": None})

    def test_a_damaged_or_forged_request_is_ignored_rather_than_obeyed(self):
        marker = self.directory / relaunch.MARKER
        for payload in (
            "[]",
            "not json",
            json.dumps({"relaunch": False}),
            json.dumps({"relaunch": "yes"}),
            json.dumps({"relaunch": True, "navigation": {"workspace": "../../etc"}}),
            json.dumps({"relaunch": True, "navigation": {"workspace": "w" * 65}}),
            json.dumps({"relaunch": True, "navigation": {"workspace": "w1", "tab": 5}}),
            json.dumps({"relaunch": True, "navigation": "everything"}),
            "{" + " " * relaunch.MAX_REQUEST_BYTES + "}",
        ):
            with self.subTest(payload=payload[:40]):
                marker.write_text(payload)
                taken = relaunch.take_request(self.directory)
                self.assertIn(taken, (None, {"navigation": None}), payload[:40])
                self.assertFalse(marker.exists(), "a consumed request was left behind")
        # Only identifiers survive; nothing else in a request is ever read.
        marker.write_text(
            json.dumps(
                {
                    "relaunch": True,
                    "socket": "/tmp/somebody-elses.sock",
                    "navigation": self.navigation(focus=True) | {"command": "rm -rf /"},
                }
            )
        )
        self.assertEqual(
            relaunch.take_request(self.directory),
            {"navigation": self.navigation(focus=True)},
        )

    def test_startup_readiness_is_recorded_for_this_window_only(self):
        self.assertFalse(relaunch.has_started(self.directory))
        relaunch.started(self.directory)
        self.assertTrue(relaunch.has_started(self.directory))
        relaunch.started(self.directory / "missing")

    def test_a_supervisor_hands_on_identifiers_and_nothing_else(self):
        carried = relaunch.navigation_argument(self.navigation(focus=True))
        self.assertEqual(relaunch.parse_navigation(carried), self.navigation(focus=True))
        for rejected in (None, {}, {"workspace": ""}, {"workspace": "../etc"}, {"tab": "t1"}):
            with self.subTest(rejected=rejected):
                self.assertIsNone(relaunch.navigation_argument(rejected))
        for text in (None, "", "{ not json", "[]", '{"workspace": "w1", "tab": 4}', "x" * 5000):
            with self.subTest(text=str(text)[:20]):
                self.assertIsNone(relaunch.parse_navigation(text))

    def test_a_window_reports_its_outcome_in_a_bounded_status_file(self):
        path = self.directory / "handover.json"
        self.assertIsNone(relaunch.read_status(path))
        relaunch.write_status(path, ready=True, relaunch=False)
        self.assertEqual(
            relaunch.read_status(path), {"ready": True, "relaunch": False, "navigation": None}
        )
        relaunch.write_status(path, ready=True, relaunch=True, selection=self.navigation())
        self.assertEqual(
            relaunch.read_status(path),
            {"ready": True, "relaunch": True, "navigation": self.navigation()},
        )
        # An unreadable, incomplete or oversized report is not a clean exit.
        for payload in ("{}", "[]", '{"ready": true}', '{"ready": "yes", "relaunch": true}'):
            path.write_text(payload)
            self.assertIsNone(relaunch.read_status(path), payload)
        path.write_text(json.dumps({"ready": True, "relaunch": True, "navigation": {"tab": "t1"}}))
        self.assertIsNone(relaunch.read_status(path)["navigation"])

    def test_carried_selection_is_applied_only_where_it_still_exists(self):
        model = Model.initial()
        model.add_tab("Second")
        model.split("right")
        space, tabs = model.space, model.space["tabs"]
        pane = leaves(tabs[1]["tree"])[1]["id"]
        carried = {
            "workspace": space["id"],
            "tab": tabs[1]["id"],
            "leaf": pane,
            "focus": True,
        }
        space["selected"] = tabs[0]["id"]
        tabs[1]["focus"] = leaves(tabs[1]["tree"])[0]["id"]
        model.state["focus"] = False
        relaunch.apply_navigation(model.state, carried)
        self.assertEqual(space["selected"], tabs[1]["id"])
        self.assertEqual(tabs[1]["focus"], pane)
        self.assertTrue(model.state["focus"])
        validate_state(model.state)

        # A peer may have closed any of it between teardown and restart.
        relaunch.apply_navigation(model.state, dict(carried, leaf="gone"))
        self.assertEqual(tabs[1]["focus"], pane)
        relaunch.apply_navigation(model.state, dict(carried, tab="gone"))
        self.assertEqual(space["selected"], tabs[1]["id"])
        relaunch.apply_navigation(model.state, dict(carried, workspace="gone"))
        relaunch.apply_navigation(model.state, None)
        relaunch.apply_navigation(model.state, {})
        validate_state(model.state)

    def test_a_missing_launcher_or_broken_settings_stop_the_refresh(self):
        keymap = self.directory / "keymap.toml"
        request = self.request(keymap_source=keymap, keymap_required=True)
        self.assertIn("file does not exist", request.check())
        keymap.write_text("prefix = 'C-a'\n")
        self.assertIsNone(request.check())
        keymap.write_text("[bindings")
        self.assertIn("keymap.toml", request.check())
        keymap.unlink()
        # An implicit default that is still absent is not an error.
        self.assertIsNone(self.request(keymap_source=keymap).check())
        # A launcher that no longer runs is found while the window is still open.
        (self.checkout / "run").write_text(
            "import sys\nprint('No module named tmux_workspaces', file=sys.stderr)\nsys.exit(1)\n"
        )
        self.assertEqual(self.request().check(), "Cannot reopen: No module named tmux_workspaces")
        (self.checkout / "run").unlink()
        self.assertIn("is missing", self.request().check())
        self.argv = ["/nonexistent/python", "-m", "tmux_workspaces"]
        self.assertIn("/nonexistent/python", self.request().check())

    def test_a_launcher_that_hangs_does_not_hold_the_confirmation(self):
        (self.checkout / "run").write_text("import time\ntime.sleep(30)\n")
        with patch.object(relaunch, "PROBE_TIMEOUT", 0.2):
            self.assertIn("did not answer in time", self.request().check())

    def test_an_unavailable_tmux_is_reported_before_the_window_closes(self):
        with patch.object(relaunch, "check_tmux", side_effect=RuntimeError("tmux not found")):
            self.assertEqual(self.request().check(), "tmux not found")

    def test_an_unwritable_window_directory_refuses_instead_of_closing(self):
        os.chmod(self.directory, 0o500)
        try:
            self.assertIn("Cannot record", self.request().check())
        finally:
            os.chmod(self.directory, 0o700)

    def test_a_window_with_fixed_keys_shows_only_its_supervisors_command(self):
        published = "/checkout/ghostty --data-dir '/lib rary'"
        request = self.request(manual_reopen=True, profile_hints=True, reopen_command=published)
        # This window's own arguments are private and must never be offered.
        private = [sys.executable, "run", "_sidebar", "--action-socket", "/tmp/actions.sock"]
        with patch.object(sys, "orig_argv", private):
            self.assertEqual(request.manual_command(), published)
            notes = " ".join(request.notes())
        self.assertIn("reopened by hand", request.check())
        self.assertFalse(request.submit(self.navigation()))
        self.assertFalse((self.directory / relaunch.MARKER).exists())
        self.assertIn("fixed", notes)
        # The command already carries the application it opens.
        self.assertNotIn("ghostty-app", notes)
        # An ordinary window never offers a command instead of refreshing.
        self.assertIsNone(self.request(reopen_command=published).manual_command())

    def test_an_unknown_install_shape_states_the_limit_instead_of_inventing_one(self):
        request = self.request(manual_reopen=True, profile_hints=True)
        self.assertIsNone(request.manual_command())
        self.assertIn("launcher you used", " ".join(request.notes()))
        oversized = self.request(manual_reopen=True, reopen_command="x" * 5000)
        self.assertIsNone(oversized.manual_command())

    def test_notes_describe_what_a_replacement_applies_and_what_it_cannot(self):
        notes = " ".join(self.request(keymap_source=Path("/tmp/keys.toml"), nested=True).notes())
        self.assertIn("keys.toml", notes)
        self.assertIn("hosting tmux", notes)
        self.assertIn("current code and settings", notes)
        self.assertIn("Shells and attached sessions keep running", notes)
        self.assertIn("Keys stay as this window started", " ".join(self.request().notes()))

    def test_recovery_names_the_command_and_hands_over_a_shell(self):
        errors = io.StringIO()
        with (
            contextlib.redirect_stderr(errors),
            patch.object(relaunch.os, "execv") as shell,
            patch.object(sys.stdin, "isatty", return_value=False),
        ):
            self.assertEqual(relaunch.recover("it closed early", "run --data-dir /library"), 1)
        shell.assert_not_called()
        self.assertIn("it closed early", errors.getvalue())
        self.assertIn("Reopen with: run --data-dir /library", errors.getvalue())
        self.assertIn("unchanged", errors.getvalue())
        # With a terminal it becomes an ordinary shell, rather than running one
        # underneath a launcher with nothing left to do.
        with (
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(relaunch.os, "execv", side_effect=OSError("fixture")) as shell,
            patch.object(sys.stdin, "isatty", return_value=True),
            patch.object(sys.stdout, "isatty", return_value=True),
            patch.dict(os.environ, {"SHELL": "/nonexistent/shell"}),
        ):
            self.assertEqual(relaunch.recover("it failed", "run"), 1)
        shell.assert_called_once_with("/bin/sh", ["/bin/sh", "-i"])


if __name__ == "__main__":
    unittest.main()
