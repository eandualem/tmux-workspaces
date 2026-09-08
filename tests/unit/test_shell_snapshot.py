import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.model import Model, leaves
from tmux_workspaces.shells import Shells


@unittest.skipUnless(shutil.which("tmux"), "tmux required for shell snapshots")
class ShellSnapshotTests(unittest.TestCase):
    def test_batch_discovery_retains_processes_and_snapshots_active_directories(self):
        with tempfile.TemporaryDirectory(prefix="tw-shell-snapshot-", dir="/tmp") as directory:
            root = Path(directory)
            other = root / "directory with spaces\nand __tw_cwd_collision__"
            other.mkdir()
            shells = Shells(str(root / "shells.sock"))
            model = Model.initial()
            model.split("right", cwd=str(other))
            panes = leaves(model.tab["tree"])
            panes[0]["cwd"] = str(root)
            try:
                with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
                    names = shells.ensure_many(panes)
                    pids = shells.tmux.run("list-panes", "-a", "-F", "#{pane_pid}")
                    self.assertEqual(shells.ensure_many(panes), names)
                    self.assertEqual(pids, shells.tmux.run("list-panes", "-a", "-F", "#{pane_pid}"))
                    for pane in panes:
                        pane["cwd"] = "/stale"
                    shells.remember_many(panes)
                    self.assertEqual(
                        [Path(pane["cwd"]).resolve() for pane in panes],
                        [root.resolve(), other.resolve()],
                    )
                    with patch("tmux_workspaces.shells.uuid.uuid4") as token:
                        token.return_value.hex = "collision"
                        shells.remember_many(panes)
                    self.assertEqual(Path(panes[1]["cwd"]).resolve(), other.resolve())
                    # A different inactive window must not replace the active
                    # shell's saved cwd when fetching all panes in one query.
                    shells.tmux.run(
                        "new-window",
                        "-d",
                        "-t",
                        "=" + names[panes[0]["id"]] + ":",
                        "-c",
                        str(other),
                        "/bin/sh",
                    )
                    shells.remember_many(panes)
                    self.assertEqual(Path(panes[0]["cwd"]).resolve(), root.resolve())
            finally:
                shells.tmux.run("kill-server", check=False)
