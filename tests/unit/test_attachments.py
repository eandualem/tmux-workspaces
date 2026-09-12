"""Ordinary attachment startup and recovery retain external-session guards."""

import struct
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tmux_workspaces.attachments import leaf_main


class StopFixture(Exception):
    pass


class AttachmentTests(unittest.TestCase):
    def test_all_owned_rgb_slots_are_reset_before_any_attachment_subprocess(self):
        from tmux_workspaces.theme import RGB_SLOTS

        expected = "".join(f"\x1b]104;{slot}\x1b\\" for slot in RGB_SLOTS)
        for agent in ("", "external"):
            with self.subTest(agent=agent):
                output = StringIO()

                def attach(*args, output=output, **kwargs):
                    self.assertEqual(output.getvalue(), expected)
                    raise StopFixture

                with (
                    redirect_stdout(output),
                    patch("tmux_workspaces.attachments.subprocess.run", side_effect=attach),
                    self.assertRaises(StopFixture),
                ):
                    leaf_main(self.args(agent=agent, terminal="" if agent else "ordinary"))

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
            redirect_stdout(StringIO()),
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


class SeparatorTests(unittest.TestCase):
    def test_named_colors_match_indexed_colors_in_both_directions(self):
        from tmux_workspaces.attachments import rule_main
        from tmux_workspaces.theme import Theme

        for color, code in (("red", 1), ("bright-white", 15), ("1", 1)):
            for vertical in (False, True):
                output = StringIO()
                with (
                    patch("fcntl.ioctl", return_value=struct.pack("HHHH", 2, 6, 0, 0)),
                    patch("tmux_workspaces.attachments.time.sleep", side_effect=StopFixture),
                    redirect_stdout(output),
                    self.assertRaises(StopFixture),
                ):
                    rule_main(
                        SimpleNamespace(
                            color=Theme.from_dict({"outline": {"foreground": color}}).separator(),
                            vertical=vertical,
                        )
                    )
                self.assertIn(f"\x1b[38;5;{code}m", output.getvalue())
                self.assertIn("│" if vertical else "─", output.getvalue())


class GroupedAttachTests(unittest.TestCase):
    """External sessions are joined through a grouped session of the viewer's own."""

    def test_the_grouped_command_turns_off_its_own_status_and_destroys_itself(self):
        from tmux_workspaces.attachments import grouped_attach_command

        command = grouped_attach_command("/tmp/src.sock", "=manager:")
        self.assertEqual(command[:4], ["tmux", "-S", "/tmp/src.sock", "new-session"])
        self.assertIn("-E", command)
        self.assertEqual(command[command.index("-t") + 1], "=manager:")
        name = command[command.index("-s") + 1]
        self.assertTrue(name.startswith("tw-"))
        # Chained options target only the grouped session, including its ownership marker.
        text = " ".join(command)
        self.assertIn(f"; set-option -t {name} @tmux_workspaces_attachment 1", text)
        self.assertIn(f"; set-option -t {name} status off", text)
        self.assertIn(f"; set-option -t {name} destroy-unattached on", text)
        self.assertNotIn("-t =manager: status", text)
        # Each client gets a name of its own.
        other = grouped_attach_command("/tmp/src.sock", "=manager:")
        self.assertNotEqual(name, other[other.index("-s") + 1])

    def test_an_agent_attaches_grouped_while_a_shell_attaches_plainly(self):
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            if command[3] == "has-session":
                # The preflight before an attach: the session exists.
                return SimpleNamespace(returncode=0)
            if command[3] == "display-message":
                return SimpleNamespace(returncode=0, stdout="$8|@12|123\n")
            if command[3] == "show-options":
                # The target's own settings, in tmux's quoting.
                return SimpleNamespace(returncode=0, stdout='mouse on\nstatus-left "a \\"b\\""\n')
            raise StopFixture

        for kind, args in (
            (
                "agent",
                SimpleNamespace(
                    agent="manager",
                    terminal="",
                    source_socket="/tmp/s.sock",
                    host_socket=None,
                    host_pane=None,
                ),
            ),
            (
                "shell",
                SimpleNamespace(
                    agent="",
                    terminal="terminal-abc",
                    source_socket="/tmp/s.sock",
                    host_socket=None,
                    host_pane=None,
                ),
            ),
        ):
            seen.clear()
            # The grouped client is a watched process; a plain attach blocks in run.
            client = MagicMock()
            client.__enter__.return_value = client
            client.poll.return_value = None
            with (
                patch("tmux_workspaces.attachments.subprocess.run", side_effect=run),
                patch("tmux_workspaces.attachments.subprocess.Popen", return_value=client) as popen,
                patch("tmux_workspaces.attachments.time.sleep", side_effect=StopFixture),
                self.assertRaises(StopFixture),
                redirect_stdout(StringIO()),
            ):
                leaf_main(args)
            if kind == "agent":
                command = popen.call_args.args[0]
                self.assertEqual(command[3], "new-session", command)
                name = command[command.index("-s") + 1]
                text = " ".join(command)
                # The target's options come first, then this session's own.
                self.assertIn(
                    f"; set-option -t {name} mouse on ; set-option -t {name} status-left", text
                )
                self.assertEqual(command[command.index("status-left") + 1], 'a "b"')
                self.assertIn(f"; set-option -t {name} destroy-unattached on", text)
                self.assertTrue(text.endswith(f"; select-window -t ={name}:@12"))
                self.assertIn(f"; set-option -t {name} status off ;", text)
                self.assertEqual(
                    [c[3] for c in seen[-3:]], ["has-session", "display-message", "show-options"]
                )
                self.assertEqual(seen[-1][5], "$8")
                self.assertEqual(command[command.index("-t") + 1], "$8")
            else:
                command = seen[-1]
                popen.assert_not_called()
                self.assertEqual(command[3], "attach-session", command)
                self.assertNotIn("status", command)

    def test_the_grouped_session_is_killed_once_its_target_is_gone(self):
        """Grouped sessions keep each other's windows alive, so the viewer's
        must not outlive the session it joined."""
        from tmux_workspaces.attachments import run_grouped_attachment

        seen = []
        client = MagicMock()
        client.__enter__.return_value = client
        # Alive through the first check; gone after the kill.
        client.poll.side_effect = [None, None, 0]

        def run(command, **kwargs):
            seen.append(command)
            if command[3] == "display-message":
                return SimpleNamespace(returncode=0, stdout="$8|@12|123\n")
            if command[3] == "has-session":
                # The original ID is gone, but a replacement already uses its name.
                return SimpleNamespace(returncode=0 if command[-1] == "=manager:" else 1)
            return SimpleNamespace(returncode=0, stdout="")

        with (
            patch("tmux_workspaces.attachments.subprocess.run", side_effect=run),
            patch("tmux_workspaces.attachments.subprocess.Popen", return_value=client) as popen,
            patch("tmux_workspaces.attachments.time.sleep"),
        ):
            run_grouped_attachment("/tmp/s.sock", "=manager:")
        name = popen.call_args.args[0][popen.call_args.args[0].index("-s") + 1]
        self.assertEqual(
            [c[3] for c in seen],
            ["display-message", "show-options", "has-session", "kill-session"],
        )
        self.assertEqual(seen[2][5], "$8")
        self.assertEqual(seen[3][4:], ["-t", name])
        client.wait.assert_called_once()

    def test_missing_session_identity_does_not_create_a_group(self):
        from tmux_workspaces.attachments import run_grouped_attachment

        for result in (
            SimpleNamespace(returncode=1, stdout=""),
            SimpleNamespace(returncode=0, stdout=""),
        ):
            with (
                patch("tmux_workspaces.attachments.subprocess.run", return_value=result),
                patch("tmux_workspaces.attachments.subprocess.Popen") as popen,
            ):
                run_grouped_attachment("/tmp/s.sock", "=manager:")
            popen.assert_not_called()
