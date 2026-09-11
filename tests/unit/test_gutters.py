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
from tmux_workspaces.tmux import Tmux


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

    def display(self, surface):
        display = Display(
            self.viewer.socket,
            str(self.root / "absent.sock"),
            self.sidebar,
            self.shells.socket,
            str(self.root / "actions.sock"),
        )
        display.style_panel("#181818", surface or "default", "#31343b")
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
        # Blank gutters hold a silent process; both separators draw one thin
        # rule, down a column between panes side by side and across a row
        # between stacked ones, so the two directions weigh the same.
        commands = [info["command"].strip('"') for info in gutters.values()]
        self.assertEqual(sum(command == GUTTER_COMMAND for command in commands), 2)
        self.assertEqual(sum("--rule" in command for command in commands), 2)
        self.assertEqual(sum("--vertical" in command for command in commands), 1)
        # Every border, the sidebar's included, vanishes into the surface; the
        # panel's own outline is what separates it.
        hidden = "fg=#1f1f1f,bg=#1f1f1f"
        for pane, info in panes.items():
            self.assertEqual(info["border"], hidden, pane)
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
                if "--rule" in i["command"]
                else "blank"
                if i["gutter"]
                else "content"
            )
            for i in top_row
        ]
        # Every gutter and content pane sits on the surface; the rule is a line.
        self.assertEqual([i["style"] for i in top_row[1:]], ["bg=#1f1f1f"] * 5, top_row)
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
        # A stacked split's separator draws one thin rule on the surface
        # instead of filling its row with the outline color.
        self.assertEqual(column[1]["style"], "bg=#1f1f1f")
        self.assertIn("--rule", column[1]["command"])
        self.assertIn("--color '#31343b'", column[1]["command"])
        self.assertEqual(column[2]["top"], column[1]["top"] + 2)

    def test_measured_ratios_round_trip_so_like_layouts_reuse_their_panes(self):
        """The separator takes cells after tmux has split, so a split is sized in
        cells from the saved ratio and the ratio measured back reproduces it.
        Two tabs with the same shape must then switch by reusing containers,
        not by killing and recreating every pane; that rebuild was slow and
        lost text typed right after a switch."""
        display = self.display("#1f1f1f")
        model = Model.initial()
        model.split("right")
        model.split("below")
        first = model.tab
        model.add_tab("second")
        for direction in ("right", "below"):
            model.split(direction)
        second = model.tab
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(first, False)
            display.remember_ratios(first["tree"])
            ratios = [first["tree"]["ratio"], first["tree"]["second"]["ratio"]]
            # A tab never shown before carries the default ratio, so its first
            # showing may rebuild; once measured, the two tabs agree.
            display.render(second, False)
            display.remember_ratios(second["tree"])
            containers = sorted(display.panes.values())
            for tab in (first, second, first, second):
                display.render(tab, False)
                self.assertEqual(sorted(display.panes.values()), containers, tab["name"])
                display.remember_ratios(tab["tree"])
        self.assertEqual([first["tree"]["ratio"], first["tree"]["second"]["ratio"]], ratios)
        self.assertEqual(second["tree"]["ratio"], first["tree"]["ratio"])
        gutters = {p for p, i in self.panes().items() if i["gutter"]}
        self.assertEqual(len(gutters), 4)

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

    def test_new_grounds_recolor_the_separator_and_the_padding(self):
        display = self.display("#1f1f1f")
        model = Model.initial()
        model.split("right")
        with patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            display.render(model.tab, False)
        display.style_panel("#202030", "#101010", "#404050")
        gutters = {p: i for p, i in self.panes().items() if i["gutter"]}
        self.assertEqual(sorted(i["style"] for i in gutters.values()), ["bg=#101010"] * 3)
        # The separator is redrawn in the new outline color by a new rule process.
        rules = [i["command"] for i in gutters.values() if "--rule" in i["command"]]
        self.assertEqual(len(rules), 1)
        self.assertIn("--color '#404050'", rules[0])
        self.assertEqual(
            self.viewer.run("show-window-options", "-gv", "pane-border-style"),
            "fg=#101010,bg=#101010",
        )
        content = {p: i["style"] for p, i in self.panes().items() if not i["gutter"]}
        self.assertTrue(all(style == "bg=#101010" for style in content.values()), content)

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
