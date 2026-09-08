import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.display import Display
from tmux_workspaces.model import Model, leaves
from tmux_workspaces.shells import Shells
from tmux_workspaces.tmux import Tmux


@unittest.skipUnless(shutil.which("tmux"), "tmux required for layout transitions")
class RenderTests(unittest.TestCase):
    def test_event_snapshot_shares_reads_and_refreshes_after_focus_resize_and_death(self):
        with tempfile.TemporaryDirectory(prefix="tw-state-", dir="/tmp") as directory:
            root = Path(directory)
            viewer = Tmux(str(root / "view.sock"))
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
                first = viewer.run("split-window", "-h", "-P", "-F", "#{pane_id}", "/bin/sh")
                second = viewer.run("split-window", "-v", "-P", "-F", "#{pane_id}", "/bin/sh")
                display = Display(
                    viewer.socket,
                    str(root / "source.sock"),
                    sidebar,
                    str(root / "shells.sock"),
                    str(root / "actions.sock"),
                )
                display.panes = {"first": first, "second": second}
                with (
                    patch.object(display.tmux, "run", wraps=display.tmux.run) as run,
                    display.snapshot_scope(),
                ):
                    initial = display.state()
                    self.assertEqual(display.focused_leaf(), "second")
                    self.assertEqual(display.size(), (160, 38))
                    self.assertIs(display.state(), initial)
                    self.assertEqual(run.call_count, 1)
                    display.select("first")
                    self.assertEqual(display.focused_leaf(), "first")
                    self.assertIsNot(display.state(), initial)
                    display.select_sidebar()
                    # Sidebar focus preserves the most recent content pane.
                    self.assertEqual(display.focused_leaf(), "first")
                    self.assertTrue(display.state().panes[sidebar].active)
                # A new event sees changes made outside the controller, including
                # resize and dead wrappers that keep their original pane IDs.
                viewer.run("resize-window", "-x", "120", "-y", "32")
                viewer.run("set-option", "-p", "-t", second, "remain-on-exit", "on")
                viewer.run("respawn-pane", "-k", "-t", second, "/usr/bin/true")
                deadline = time.monotonic() + 5
                while viewer.run("display-message", "-p", "-t", second, "#{pane_dead}") != "1":
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                with display.snapshot_scope():
                    self.assertEqual(display.size(), (120, 32))
                    self.assertTrue(display.state().panes[second].dead)
                # Exceptions must not leave an event snapshot installed.
                with (
                    self.assertRaisesRegex(ValueError, "event failed"),
                    display.snapshot_scope(),
                ):
                    display.state()
                    raise ValueError("event failed")
                viewer.run("select-pane", "-t", first)
                self.assertTrue(display.state().panes[first].active)
            finally:
                viewer.run("kill-server", check=False)

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
                    clients = viewer.run("list-panes", "-F", "#{pane_id}:#{pane_pid}")
                    # Name and saved cwd changes do not replace attachment
                    # clients, even after recording actual rounded split ratios.
                    display.remember_ratios(four["tree"])
                    four["name"] = "Renamed without redraw"
                    leaves(four["tree"])[0]["cwd"] = str(root)
                    display.render(four, False)
                    self.assertEqual(
                        clients, viewer.run("list-panes", "-F", "#{pane_id}:#{pane_pid}")
                    )
                    dead = display.panes[leaves(four["tree"])[0]["id"]]
                    viewer.run("set-option", "-p", "-t", dead, "remain-on-exit", "on")
                    viewer.run("respawn-pane", "-k", "-t", dead, "/usr/bin/true")
                    deadline = time.monotonic() + 5
                    while viewer.run("display-message", "-p", "-t", dead, "#{pane_dead}") != "1":
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                    # A dead client keeps its pane ID; reuse must detect that.
                    display.render(four, False)
                    self.assertEqual(
                        viewer.run("display-message", "-p", "-t", dead, "#{pane_dead}"), "0"
                    )
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
                        with display.snapshot_scope():
                            before = display.state()
                            display.render(tab, False)
                            after = display.state()
                            self.assertIsNot(after, before)
                            if tab:
                                self.assertEqual(
                                    set(after.panes), {sidebar, *display.panes.values()}
                                )
                        self.assertTrue(all(width == 28 for _operation, width in samples), samples)
                        if tab is None:
                            self.assertEqual(
                                viewer.run("list-panes", "-F", "#{@viewer_leaf_id}").strip(), ""
                            )
                        else:
                            actual = viewer.run(
                                "list-panes", "-F", "#{@viewer_tab_id}:#{@viewer_leaf_id}"
                            ).splitlines()
                            self.assertEqual(
                                {line for line in actual if line != ":"},
                                {tab["id"] + ":" + p["id"] for p in leaves(tab["tree"])},
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
                    # Direct callers can supply stale focus; Model(state) does
                    # not normalize it, and focus mode explicitly falls back to
                    # the first leaf. Rendering must not crash or select an
                    # unrelated pane when the requested leaf is absent.
                    stale = dict(four, focus="missing-leaf")
                    for focused in (True, False):
                        with self.subTest(stale_focus_mode=focused), display.snapshot_scope():
                            display.select_sidebar()
                            display.render(stale, focused)
                            self.assertTrue(display.state().panes[sidebar].active)
                            expected = leaves(four["tree"])[:1] if focused else leaves(four["tree"])
                            self.assertEqual(set(display.panes), {pane["id"] for pane in expected})
                            for leaf_id, pane_id in display.panes.items():
                                self.assertEqual(
                                    viewer.run(
                                        "display-message",
                                        "-p",
                                        "-t",
                                        pane_id,
                                        "#{@viewer_tab_id}:#{@viewer_leaf_id}",
                                    ),
                                    four["id"] + ":" + leaf_id,
                                )
            finally:
                viewer.run("kill-server", check=False)
                shells.run("kill-server", check=False)


if __name__ == "__main__":
    unittest.main()
