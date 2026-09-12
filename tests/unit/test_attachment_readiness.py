"""An ordinary shell is ready only on the current viewer pane's attachment."""

import contextlib
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tmux_workspaces.controls import Actions
from tmux_workspaces.display import Display
from tmux_workspaces.sidebar import Sidebar


class AttachmentReadinessTests(unittest.TestCase):
    def setUp(self):
        self.leaf = {"id": "abcdefabcdef", "agent": None}
        self.tab = {"id": "tab", "tree": self.leaf, "focus": "abcdefabcdef"}
        self.display = Display(
            "/unused/viewer", "/unused/external", "%0", "/unused/shell", "/unused/action"
        )
        self.display.panes = {"abcdefabcdef": "%1"}
        self.row = "%1|abcdefabcdef|tab|1|0|/dev/ttys123|567"
        self.display.tmux.run = Mock(return_value=self.row)
        self.display.shells.tmux.run = Mock(return_value="terminal-abcdefabcdef|/dev/ttys123")
        self.display.select_sidebar = Mock()

    def test_another_viewer_or_wrong_session_cannot_release_input(self):
        self.display.shells.tmux.run.side_effect = [
            "terminal-abcdefabcdef|/dev/other\nterminal-other|/dev/ttys123",
            "terminal-abcdefabcdef|/dev/ttys123",
        ]
        self.display.wait_for_input(self.tab)
        self.assertEqual(self.display.shells.tmux.run.call_count, 2)
        self.assertEqual(self.display.tmux.run.call_count, 3)
        self.display.select_sidebar.assert_not_called()
        for query in self.display.shells.tmux.run.call_args_list:
            self.assertGreater(query.kwargs["timeout"], 0)
            self.assertLessEqual(query.kwargs["timeout"], 3)

    def test_sidebar_external_and_empty_panes_never_wait_on_a_source(self):
        for kind in ("sidebar", "external", "empty", "no-tab"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "sidebar":
                    self.display.tmux.run.return_value = "%0|||1|0|/dev/panel|123"
                elif kind == "external":
                    self.leaf["agent"] = "offline-external"
                elif kind == "empty":
                    self.leaf["empty"] = True
                self.display.wait_for_input(None if kind == "no-tab" else self.tab)
                self.display.shells.tmux.run.assert_not_called()
                self.display.select_sidebar.assert_not_called()

    def test_ready_client_with_replaced_dead_or_missing_viewer_is_rejected(self):
        for changed in (
            self.row.replace("|567", "|568"),
            self.row.replace("|1|0|", "|1|1|"),
            self.row.replace("abcdefabcdef|tab", "replacement|tab"),
            self.row.replace("|tab|", "|other-tab|"),
            self.row.replace("/dev/ttys123", "/dev/reused"),
            "%0|||1|0|/dev/panel|123",
            "",
        ):
            with self.subTest(changed=changed):
                self.display.tmux.run.side_effect = [self.row, changed]
                with self.assertRaisesRegex(RuntimeError, "attachment changed"):
                    self.display.wait_for_input(self.tab)
                self.display.select_sidebar.assert_called()

    def second_pane(self):
        second = {"id": "fedcbafedcba", "agent": None}
        self.tab["tree"] = {"first": self.leaf, "second": second}
        self.display.panes["fedcbafedcba"] = "%2"
        return second, "%2|fedcbafedcba|tab|1|0|/dev/ttys456|890"

    def test_focus_change_waits_for_the_new_panes_own_client(self):
        _, second = self.second_pane()
        switched = self.row.replace("|1|0|", "|0|0|") + "\n" + second
        self.display.tmux.run.side_effect = [self.row, switched, switched, switched]
        self.display.shells.tmux.run.side_effect = [
            "terminal-abcdefabcdef|/dev/ttys123",
            "terminal-fedcbafedcba|/dev/other",
            "terminal-fedcbafedcba|/dev/ttys456",
        ]
        self.display.wait_for_input(self.tab)
        self.assertEqual(self.display.shells.tmux.run.call_count, 3)
        self.display.select_sidebar.assert_not_called()

    def test_focus_change_to_sidebar_external_or_empty_stops_waiting(self):
        for kind in ("sidebar", "external", "empty"):
            with self.subTest(kind=kind):
                self.setUp()
                leaf, second = self.second_pane()
                if kind == "sidebar":
                    second = "%0|||1|0|/dev/panel|123"
                elif kind == "external":
                    leaf["agent"] = "offline-external"
                else:
                    leaf["empty"] = True
                self.display.tmux.run.side_effect = [
                    self.row,
                    self.row.replace("|1|0|", "|0|0|") + "\n" + second,
                ]
                self.display.wait_for_input(self.tab)
                self.assertEqual(self.display.shells.tmux.run.call_count, 1)
                self.display.select_sidebar.assert_not_called()

    def test_focus_change_still_rejects_changed_original_identity(self):
        _, second = self.second_pane()
        inactive = self.row.replace("|1|0|", "|0|0|")
        for changed in (
            inactive.replace("|567", "|568"),
            inactive.replace("|0|0|", "|0|1|"),
            inactive.replace("abcdefabcdef|tab", "replacement|tab"),
            inactive.replace("|tab|", "|other-tab|"),
            inactive.replace("/dev/ttys123", "/dev/reused"),
            "",
        ):
            with self.subTest(changed=changed):
                self.display.tmux.run.side_effect = [self.row, changed + "\n" + second]
                with self.assertRaisesRegex(RuntimeError, "attachment changed"):
                    self.display.wait_for_input(self.tab)

    def test_original_identity_remains_protected_while_new_pane_attaches(self):
        _, second = self.second_pane()
        switched = self.row.replace("|1|0|", "|0|0|") + "\n" + second
        self.display.tmux.run.side_effect = [self.row, switched, switched.replace("|567", "|568")]
        self.display.shells.tmux.run.return_value = ""
        with self.assertRaisesRegex(RuntimeError, "attachment changed"):
            self.display.wait_for_input(self.tab)
        self.assertEqual(self.display.shells.tmux.run.call_count, 2)

    def test_focus_change_cannot_restart_the_attachment_deadline(self):
        _, second = self.second_pane()
        self.display.tmux.run.side_effect = [
            self.row,
            self.row.replace("|1|0|", "|0|0|") + "\n" + second,
        ]
        with (
            patch("tmux_workspaces.display.time.monotonic", side_effect=[0, 0.1, 0.4, 0.8, 1.01]),
            self.assertRaisesRegex(RuntimeError, "not ready"),
        ):
            self.display.wait_for_input(self.tab, timeout=1)
        self.assertEqual(self.display.shells.tmux.run.call_count, 1)
        self.display.select_sidebar.assert_called_once()

    def test_focus_change_rejects_unknown_or_retargeted_destination(self):
        _, second = self.second_pane()
        for changed in (second.replace("%2", "%99"), second.replace("|tab|", "|other-tab|")):
            with self.subTest(changed=changed):
                self.display.tmux.run.side_effect = [
                    self.row,
                    self.row.replace("|1|0|", "|0|0|") + "\n" + changed,
                ]
                with self.assertRaisesRegex(RuntimeError, "attachment (changed|disappeared)"):
                    self.display.wait_for_input(self.tab)

    def test_missing_client_times_out_and_returns_focus_to_navigation(self):
        self.display.shells.tmux.run.return_value = ""
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.display.wait_for_input(self.tab, timeout=0.02)
        self.display.select_sidebar.assert_called_once()

    def test_dead_pane_does_not_wait_for_a_source(self):
        self.display.tmux.run.return_value = self.row.replace("|1|0|", "|1|1|")
        with self.assertRaisesRegex(RuntimeError, "attachment changed"):
            self.display.wait_for_input(self.tab)
        self.display.shells.tmux.run.assert_not_called()
        self.display.select_sidebar.assert_called_once()

    def test_unresponsive_server_is_recoverable(self):
        self.display.shells.tmux.run.side_effect = subprocess.TimeoutExpired("tmux", 3)
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.display.wait_for_input(self.tab)
        self.display.select_sidebar.assert_called_once()


class ActionAcknowledgementTests(unittest.TestCase):
    def test_sidebar_checks_readiness_before_ack_and_closes_failed_actions(self):
        with (
            tempfile.TemporaryDirectory(prefix="tw-ready-ack-", dir="/tmp") as directory,
            contextlib.closing(Actions(str(Path(directory) / "actions"))) as actions,
            socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender,
        ):
            sender.bind(str(Path(directory) / "reply"))
            sender.setblocking(False)
            sidebar = Sidebar.__new__(Sidebar)
            sidebar.actions = actions
            sidebar.running = True
            sidebar.config_popup = None
            sidebar.model = Mock(tab={"id": "tab"})
            sidebar.display = Mock()
            sidebar.display.snapshot_scope.side_effect = contextlib.nullcontext
            sidebar.action = Mock()
            sidebar.draw = Mock()

            for failed in (False, True):
                with self.subTest(failed=failed):

                    def ready(tab, failed=failed):
                        self.assertEqual(tab, sidebar.model.tab)
                        sidebar.action.assert_called_with("next-workspace")
                        sidebar.draw.assert_not_called()
                        with self.assertRaises(BlockingIOError):
                            sender.recv(16)
                        if failed:
                            raise RuntimeError("attachment failed")

                    sidebar.draw.reset_mock()
                    sidebar.display.wait_for_input.side_effect = ready
                    sender.sendto(b"next-workspace", actions.path)
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, "attachment failed"):
                            sidebar.apply_pending()
                        sidebar.draw.assert_not_called()
                    else:
                        self.assertTrue(sidebar.apply_pending())
                        sidebar.draw.assert_called_once()
                    self.assertEqual(sender.recv(16), b"failed" if failed else b"applied")

    def test_normal_completion_acks_but_close_break_and_consumer_failure_do_not(self):
        with (
            tempfile.TemporaryDirectory(prefix="tw-ack-", dir="/tmp") as directory,
            contextlib.closing(Actions(str(Path(directory) / "actions"))) as actions,
            socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender,
        ):
            sender.bind(str(Path(directory) / "reply"))
            sender.settimeout(1)
            for ending in ("complete", "close", "break", "exception"):
                with self.subTest(ending=ending):
                    sender.sendto(b"next-workspace", actions.path)
                    if ending == "complete":
                        self.assertEqual(list(actions.pending()), ["next-workspace"])
                    elif ending == "close":
                        pending = actions.pending()
                        self.assertEqual(next(pending), "next-workspace")
                        pending.close()
                    elif ending == "break":
                        with contextlib.closing(actions.pending()) as pending:
                            for _action in pending:
                                break
                    else:
                        with (
                            self.assertRaisesRegex(RuntimeError, "attachment failed"),
                            contextlib.closing(actions.pending()) as pending,
                        ):
                            for _action in pending:
                                raise RuntimeError("attachment failed")
                    self.assertEqual(
                        sender.recv(16), b"applied" if ending == "complete" else b"failed"
                    )
