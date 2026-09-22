"""Literal tmux arguments survive its separate command queue parser."""

import os
import shutil
import unittest
from unittest.mock import patch

from tests.integration.support import FixtureResources, wait
from tmux_workspaces.attachments import grouped_attach_command, target_session_options
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import clean_env


@unittest.skipUnless(shutil.which("tmux"), "tmux required for argument and attachment checks")
class TmuxArgumentTests(unittest.TestCase):
    def test_shell_cwd_and_option_values_preserve_terminal_semicolons(self):
        with (
            FixtureResources(prefix="tw-arguments-") as resources,
            patch.dict(
                os.environ,
                clean_env() | {"HOME": str(resources.root), "SHELL": "/bin/sh"},
                clear=True,
            ),
        ):
            server = resources.server()
            cwd = resources.root / "ordinary;"
            cwd.mkdir()
            pane = {"id": "012345abcdef", "agent": None, "cwd": str(cwd)}
            name = Shells(server.socket).ensure(pane)
            target = "=" + name + ":"
            self.assertEqual(
                server.run("display-message", "-p", "-t", target, "#{pane_current_path}"),
                str(cwd),
            )
            server.run("set-option", "-t", target, "@single", r"value\;")
            server.batch(
                [
                    ["set-option", "-t", target, "@literal", ";"],
                    ["set-option", "-t", target, "@after", "queue continued"],
                ]
            )
            for option, value in (
                ("@single", r"value\;"),
                ("@literal", ";"),
                ("@after", "queue continued"),
            ):
                self.assertEqual(server.run("show-options", "-qv", "-t", target, option), value)

    def test_grouped_attachment_copies_literals_and_disappears_on_detach(self):
        with (
            FixtureResources(prefix="tw-group-arguments-") as resources,
            patch.dict(
                os.environ,
                clean_env() | {"HOME": str(resources.root), "SHELL": "/bin/sh"},
                clear=True,
            ),
        ):
            source = resources.server()
            source.run("-f", "/dev/null", "new-session", "-d", "-s", "source", "/bin/sh")
            source.run("set-option", "-t", "=source:", "@literal", ";")
            source.run("set-option", "-t", "=source:", "@slash", r"value\;")
            original = source.run("show-options", "-t", "=source:")
            shell_pid = source.run("display-message", "-p", "-t", "=source:", "#{pane_pid}")
            command = grouped_attach_command(
                source.socket,
                "=source:",
                "tw-argument-helper",
                target_session_options(source.socket, "=source:"),
            )
            client = resources.client([], launcher=command)
            target = "=tw-argument-helper:"
            wait(
                client,
                lambda: (
                    source.run(
                        "show-options", "-qv", "-t", target, "destroy-unattached", check=False
                    )
                    == "on"
                ),
                "grouped attachment did not finish its option queue",
            )
            self.assertEqual(source.run("show-options", "-qv", "-t", target, "@literal"), ";")
            self.assertEqual(source.run("show-options", "-qv", "-t", target, "@slash"), r"value\;")
            source.run("detach-client", "-s", target)
            self.assertEqual(client.process.wait(timeout=3), 0)
            self.assertEqual(source.run("list-sessions", "-F", "#{session_name}"), "source")
            self.assertEqual(source.run("show-options", "-t", "=source:"), original)
            self.assertEqual(
                source.run("display-message", "-p", "-t", "=source:", "#{pane_pid}"), shell_pid
            )
