import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Model, leaves
from terminal import Display, Shells, Tmux


@unittest.skipUnless(shutil.which("tmux"), "tmux required for layout transitions")
class RenderTests(unittest.TestCase):
    def test_sidebar_never_expands_between_four_single_and_empty_layouts(self):
        with tempfile.TemporaryDirectory(prefix="tw-render-", dir="/tmp") as directory:
            root = Path(directory)
            viewer, shells = Tmux(str(root / "view.sock")), Tmux(str(root / "shells.sock"))
            try:
                viewer.run(
                    "-f",
                    "/dev/null",
                    "new-session",
                    "-d",
                    "-s",
                    "viewer",
                    "-x",
                    "160",
                    "-y",
                    "38",
                    "/bin/sh",
                )
                sidebar = viewer.run("display-message", "-p", "#{pane_id}")
                display = Display(
                    viewer.socket,
                    str(root / "absent.sock"),
                    sidebar,
                    shells.socket,
                    str(root / "actions.sock"),
                )
                model = Model.initial()
                for direction in ("right", "below", "right"):
                    model.split(direction)
                four, single = model.tab, Model.initial().tab
                with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
                    display.render(four, False)
                    pids = {
                        pane["id"]: shells.run(
                            "display-message",
                            "-p",
                            "-t",
                            "=" + Shells.name(pane) + ":",
                            "#{pane_pid}",
                        )
                        for pane in leaves(four["tree"])
                    }
                    original = display.tmux.run
                    samples = []

                    def checked(*args, **kwargs):
                        result = original(*args, **kwargs)
                        width = int(
                            viewer.run("display-message", "-p", "-t", sidebar, "#{pane_width}")
                        )
                        samples.append((args[0], width))
                        return result

                    display.tmux.run = checked
                    for tab in (single, four, None, single, four):
                        display.render(tab, False)
                        self.assertTrue(all(width == 28 for _operation, width in samples), samples)
                        if tab is None:
                            self.assertEqual(
                                viewer.run("list-panes", "-F", "#{@viewer_leaf_id}").strip(), ""
                            )
                        for pane in leaves(four["tree"]):
                            self.assertEqual(
                                pids[pane["id"]],
                                shells.run(
                                    "display-message",
                                    "-p",
                                    "-t",
                                    "=" + Shells.name(pane) + ":",
                                    "#{pane_pid}",
                                ),
                            )
            finally:
                viewer.run("kill-server", check=False)
                shells.run("kill-server", check=False)


if __name__ == "__main__":
    unittest.main()
