"""Ordinary attachment startup and recovery retain external-session guards."""

import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from tmux_workspaces.attachments import leaf_main


class StopFixture(Exception):
    pass


class AttachmentTests(unittest.TestCase):
    def args(self, *, agent="", terminal="ordinary"):
        return SimpleNamespace(
            agent=agent,
            terminal=terminal,
            source_socket="/unused/source.sock",
            host_socket=None,
            host_pane=None,
        )

    def test_owned_terminal_attempts_attach_then_recovers_when_first_attempt_fails(self):
        success = subprocess.CompletedProcess([], 0)
        missing = subprocess.CompletedProcess([], 1)
        with (
            patch(
                "tmux_workspaces.attachments.subprocess.run",
                side_effect=[missing, success, success],
            ) as run,
            patch("tmux_workspaces.attachments.time.sleep", side_effect=[None, StopFixture]),
            self.assertRaises(StopFixture),
        ):
            leaf_main(self.args())
        self.assertEqual(
            [call.args[0][3] for call in run.call_args_list],
            [
                "attach-session",
                "has-session",
                "attach-session",
            ],
        )
        self.assertTrue(all(call.args[0][-1] == "=ordinary:" for call in run.call_args_list))

    def test_owned_terminal_still_refuses_a_session_that_hosts_the_viewer(self):
        args = self.args()
        args.host_socket, args.host_pane = args.source_socket, "%8"
        output = StringIO()
        with (
            patch(
                "tmux_workspaces.attachments.attachment_hosts_viewer", return_value=True
            ) as hosts,
            patch(
                "tmux_workspaces.attachments.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
            patch("tmux_workspaces.attachments.time.sleep", side_effect=StopFixture),
            redirect_stdout(output),
            self.assertRaises(StopFixture),
        ):
            leaf_main(args)
        hosts.assert_called_with(args, "=ordinary:")
        self.assertEqual([call.args[0][3] for call in run.call_args_list], ["has-session"])
        self.assertIn("This session hosts the viewer", output.getvalue())

    def test_external_attachment_retains_preflight_and_offline_notice(self):
        output = StringIO()
        with (
            patch(
                "tmux_workspaces.attachments.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1),
            ) as run,
            patch("tmux_workspaces.attachments.time.sleep", side_effect=StopFixture),
            redirect_stdout(output),
            self.assertRaises(StopFixture),
        ):
            leaf_main(self.args(agent="external", terminal=""))
        self.assertEqual([call.args[0][3] for call in run.call_args_list], ["has-session"])
        self.assertIn("external — session offline", output.getvalue())
