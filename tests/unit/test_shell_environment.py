"""Narrow shell-session context without forwarding launcher metadata."""

import os
import shlex
import unittest
from unittest.mock import patch

from tmux_workspaces.shells import shell_command
from tmux_workspaces.tmux import SHELL_CONTEXT_NAMES, clean_env, shell_context


class ShellEnvironmentTests(unittest.TestCase):
    def test_session_paths_are_explicit_and_control_environment_stays_filtered(self):
        context = {name: "/synthetic/" + name for name in SHELL_CONTEXT_NAMES}
        excluded = {
            "TMUX": "private-server,123,0",
            "TMUX_PANE": "%99",
            "BACKBONE_API_KEY": "synthetic-secret",
            "BACKBONE_DATA_DIR": "/synthetic/backbone",
            "OPENAI_API_KEY": "synthetic-secret",
            "AWS_SECRET_ACCESS_KEY": "synthetic-secret",
            "SSH_AGENT_PID": "123",
            "SSH_CONNECTION": "synthetic connection",
            "XDG_UNRECOGNIZED_SECRET": "synthetic-secret",
        }
        with patch.dict(os.environ, context | excluded | {"PATH": "/bin"}, clear=True):
            self.assertEqual(shell_context(), context)
            self.assertEqual(clean_env(), {"PATH": "/bin"})

    def test_shell_command_unsets_missing_context_and_quotes_present_values(self):
        value = "/mock agent/' socket; $(exit 42)\nnext"
        with patch.dict(os.environ, {"SSH_AUTH_SOCK": value}, clear=True):
            command = shlex.split(shell_command("/custom shell"))
        self.assertEqual(command[:5], ["env", "-u", "TMUX", "-u", "TMUX_PANE"])
        for name in SHELL_CONTEXT_NAMES:
            index = command.index(name)
            self.assertEqual(command[index - 1], "-u")
        self.assertEqual(command[-4:], ["SSH_AUTH_SOCK=" + value, "/custom shell", "-l", "-i"])
        self.assertFalse(any(item.startswith("XDG_CONFIG_HOME=") for item in command))
