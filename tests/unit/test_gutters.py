"""Padding inside content panes: gutter panes, hidden borders and the background
query that makes them possible."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

from tmux_workspaces.display import GUTTER_COMMAND, Display, gutter_allowance
from tmux_workspaces.model import Model, leaves
from tmux_workspaces.terminal_colors import parse_reply
from tmux_workspaces.tmux import Tmux


class BackgroundReplyTests(unittest.TestCase):
    def test_terminal_replies_are_read_in_both_spellings(self):
        self.assertEqual(parse_reply(b"\x1b]11;rgb:1e1e/2222/2626\x1b\\"), "#1e2226")
        self.assertEqual(parse_reply(b"\x1b]11;rgb:1f/1f/1f\x07"), "#1f1f1f")
        self.assertEqual(parse_reply(b"noise\x1b]11;#1F1F1F\x07"), "#1f1f1f")
        self.assertIsNone(parse_reply(b""))
        self.assertIsNone(parse_reply(b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"))


class AllowanceTests(unittest.TestCase):
    def test_gutters_add_to_the_minimum_size_per_split(self):
        model = Model.initial()
        self.assertEqual(gutter_allowance(None), (0, 0))
        self.assertEqual(gutter_allowance(model.tab["tree"]), (4, 0))
        model.split("right")
        self.assertEqual(gutter_allowance(model.tab["tree"]), (6, 0))
        model.split("below")
        self.assertEqual(gutter_allowance(model.tab["tree"]), (6, 2))


@unittest.skipUnless(shutil.which("tmux"), "tmux required for layout rendering")
class GutterLayoutTests(unittest.TestCase):
    """Rendered against a private tmux server with a client attached, since the
    display's own options retire an unattached server."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tw-gutter-", dir="/tmp")
        root = Path(self.directory.name)
        self.viewer = Tmux(str(root / "view.sock"))
        self.shells = Tmux(str(root / "shells.sock"))
        self.outer = Tmux(str(root / "outer.sock"))
        self.viewer.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "viewer",
            "-x",
            "170",
            "-y",
            "40",
            "/bin/sh",
        )
        self.sidebar = self.viewer.run("display-message", "-p", "#{pane_id}")
        self.outer.run(
            "-f",
            "/dev/null",
            "new-session",
            "-d",
            "-s",
            "outer",
            "-x",
            "170",
            "-y",
            "40",
            f"tmux -S {self.viewer.socket} attach -t viewer",
        )
        time.sleep(0.6)
        self.root = root

    def tearDown(self):
        for server in (self.outer, self.viewer, self.shells):
            subprocess.run(["tmux", "-S", server.socket, "kill-server"], capture_output=True)
        self.directory.cleanup()

    def display(self, background):
        display = Display(
            self.viewer.socket,
            str(self.root / "absent.sock"),
            self.sidebar,
            self.shells.socket,
            str(self.root / "actions.sock"),
            background=background,
        )
        display.style_panel("#181818")
        display.setup()
        return display

    def panes(self):
        rows = {}
        for line in self.viewer.run(
            "list-panes",
            "-F",
            "#{pane_id} #{?@viewer_gutter,1,0} #{pane_left} #{pane_top} #{pane_width} "
            "#{pane_height} #{window-style} #{pane-border-style} #{pane_start_command}",
        ).splitlines():
            pane, gutter, left, top, width, height, style, border, *command = line.split()
            rows[pane] = {
                "gutter": gutter == "1",
                "left": int(left),
                "top": int(top),
                "width": int(width),
                "height": int(height),
                "style": style,
                "border": border,
                "command": " ".join(command),
            }
        return rows

    def test_three_panes_are_padded_and_separated_by_bands(self):
        display = self.display("#1f1f1f")
        model = Model.initial()
        model.split("right")
        model.split("below")
        tab = model.tab
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(tab, False)
        panes = self.panes()
        gutters = {pane: info for pane, info in panes.items() if info["gutter"]}
        content = {pane: info for pane, info in panes.items() if not info["gutter"]}
        # Sidebar plus the three leaves; two edge gutters, one band per split.
        self.assertEqual(set(content) - {self.sidebar}, set(display.panes.values()))
        self.assertEqual(len(display.panes), len(leaves(tab["tree"])))
        self.assertEqual(len(gutters), 4)
        # tmux quotes the start command it reports.
        self.assertTrue(
            all(info["command"].strip('"') == GUTTER_COMMAND for info in gutters.values())
        )
        hidden = "fg=#1f1f1f,bg=#1f1f1f"
        for pane, info in panes.items():
            if pane != self.sidebar:
                self.assertEqual(info["border"], hidden, pane)
        self.assertEqual(panes[self.sidebar]["border"], "fg=#181818,bg=#181818")
        # Reading left to right along the top row: sidebar, band border, blank
        # gutter, hidden border, content, hidden border, band gutter, hidden
        # border, content, hidden border, blank gutter at the window's edge.
        top_row = sorted(
            (info for info in panes.values() if info["top"] == 0), key=lambda i: i["left"]
        )
        kinds = [
            (
                "sidebar"
                if i is panes[self.sidebar]
                else "band"
                if i["style"].startswith("bg=")
                else "blank"
                if i["gutter"]
                else "content"
            )
            for i in top_row
        ]
        self.assertEqual(kinds, ["sidebar", "blank", "content", "band", "content", "blank"])
        for earlier, later in pairwise(top_row):
            self.assertEqual(later["left"], earlier["left"] + earlier["width"] + 1)
        self.assertEqual(top_row[-1]["left"] + top_row[-1]["width"], 170)
        # The vertical split on the right carries a one-row band between its panes.
        column = sorted(
            (info for info in panes.values() if info["left"] == top_row[4]["left"]),
            key=lambda i: i["top"],
        )
        self.assertEqual([i["gutter"] for i in column], [False, True, False])
        self.assertEqual(column[1]["height"], 1)
        self.assertTrue(column[1]["style"].startswith("bg="))
        self.assertEqual(column[2]["top"], column[1]["top"] + 2)

    def test_the_same_tab_is_not_rebuilt_and_a_click_on_a_gutter_bounces(self):
        display = self.display("#1f1f1f")
        model = Model.initial()
        model.split("right")
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(model.tab, False)
            before = dict(display.panes)
            gutters_before = {p for p, i in self.panes().items() if i["gutter"]}
            display.render(model.tab, False)
        self.assertEqual(display.panes, before)
        self.assertEqual({p for p, i in self.panes().items() if i["gutter"]}, gutters_before)
        leaf, content = next(iter(before.items()))
        self.viewer.run("select-pane", "-t", content)
        self.viewer.run("select-pane", "-t", sorted(gutters_before)[0])
        time.sleep(0.2)
        self.assertEqual(self.viewer.run("display-message", "-p", "#{pane_id}"), content)
        self.assertEqual(display.focused_leaf(), leaf)

    def test_a_new_panel_color_recolors_the_bands_and_keeps_the_padding_blank(self):
        display = self.display("#1f1f1f")
        model = Model.initial()
        model.split("right")
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(model.tab, False)
        display.style_panel("#202030")
        styles = {p: i["style"] for p, i in self.panes().items() if i["gutter"]}
        self.assertEqual(sorted(styles.values()), ["bg=#202030", "default", "default"])
        self.assertEqual(
            self.viewer.run("show-window-options", "-gv", "pane-border-style"),
            "fg=#202030,bg=#202030",
        )

    def test_without_a_background_there_are_no_gutters_and_borders_are_the_band(self):
        display = self.display(None)
        model = Model.initial()
        model.split("right")
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(model.tab, False)
        panes = self.panes()
        self.assertFalse(any(info["gutter"] for info in panes.values()))
        self.assertEqual(len(panes), 3)
        # Nothing is set per pane, so every border inherits the window's band.
        band = "fg=#181818,bg=#181818"
        self.assertTrue(all(info["border"] == band for info in panes.values()))


if __name__ == "__main__":
    unittest.main()
