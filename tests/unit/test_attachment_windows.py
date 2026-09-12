"""Viewer window hints belong to one marked helper and remain source read-only."""

import shlex
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from tmux_workspaces.attachments import run_grouped_attachment
from tmux_workspaces.display import Display


class AttachmentWindowTests(unittest.TestCase):
    def test_saved_window_must_still_belong_to_the_immutable_source_session(self):
        for saved, listed, expected in (
            ("", "", "@12"),
            ("123:$8:@17", "@12\n@17", "@17"),
            ("123:$8:@99", "@12\n@17", "@12"),
            ("456:$8:@17", "@12\n@17", "@12"),
            ("123:$9:@17", "@12\n@17", "@12"),
            ("@17", "@12\n@17", "@12"),
        ):
            with self.subTest(saved=saved):

                def run(command, listed=listed, **kwargs):
                    if command[3] == "display-message":
                        return SimpleNamespace(returncode=0, stdout="$8|@12|123\n")
                    if command[3] == "list-windows":
                        self.assertEqual(command[5], "$8")
                        return SimpleNamespace(returncode=0, stdout=listed)
                    self.fail(f"unexpected source command {command}")

                client = MagicMock()
                client.__enter__.return_value = client
                client.poll.return_value = 0
                with (
                    patch("tmux_workspaces.attachments.subprocess.run", side_effect=run),
                    patch("tmux_workspaces.attachments.target_session_options", return_value=[]),
                    patch(
                        "tmux_workspaces.attachments.subprocess.Popen", return_value=client
                    ) as spawn,
                ):
                    run_grouped_attachment("/unused/source", "=sample:", saved)
                command = spawn.call_args.args[0]
                helper = command[command.index("-s") + 1]
                self.assertEqual(
                    command[-4:], [";", "select-window", "-t", f"={helper}:{expected}"]
                )
                self.assertEqual(command[command.index("-t") + 1], "$8")

    def test_only_the_marked_client_on_this_viewer_tty_is_remembered(self):
        display = Display(
            "/unused/viewer", "/unused/source", "%0", "/unused/shell", "/unused/actions"
        )
        display.panes = {"leaf": "%1"}
        display._external_targets = {"leaf": ("/unused/source", "sample")}
        display.tmux.run = Mock(return_value="%0|/dev/panel\n%1|/dev/owned")
        source = Mock()
        key = ("leaf", "/unused/source", "sample")
        for listed, expected in (
            ("/dev/other|@19|1|123|$8\n/dev/owned|@12|0|123|$8", None),
            ("/dev/other|@19|1|123|$8\n/dev/owned|@12|1|123|$8", "123:$8:@12"),
        ):
            with self.subTest(listed=listed):
                source.run.return_value = listed
                with patch("tmux_workspaces.display.Tmux", return_value=source):
                    display._capture_attachment_windows()
                self.assertEqual(display._attachment_windows.get(key), expected)
        source.run.side_effect = subprocess.TimeoutExpired("tmux", 0.5)
        with patch("tmux_workspaces.display.Tmux", return_value=source):
            display._capture_attachment_windows()
        self.assertEqual(display._attachment_windows[key], "123:$8:@12")

    def test_hint_is_not_reused_when_the_leaf_attaches_a_different_source_or_session(self):
        display = Display(
            "/unused/viewer", "/unused/source", "%0", "/unused/shell", "/unused/actions"
        )
        display._attachment_windows[("leaf", "/unused/source", "sample")] = "123:$8:@12"
        pane = {"id": "leaf", "agent": "sample"}
        self.assertIn("123:$8:@12", shlex.split(display._leaf_command(pane)))
        self.assertNotIn("--attachment-window", display._leaf_command(pane | {"agent": "other"}))
        self.assertNotIn(
            "--attachment-window", display._leaf_command(pane | {"source_socket": "/other/source"})
        )
