"""The lower panel: who the focused pane is, one Configure… menu, and the
workspace icon row anchored at the bottom."""

from __future__ import annotations

import contextlib
import curses
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.keymap import DEFAULT_KEYMAP
from tmux_workspaces.layout_validation import InvalidLayout, validate_state
from tmux_workspaces.menu import RULE
from tmux_workspaces.model import Model, shared_layout
from tmux_workspaces.sidebar import WORKSPACE_ICONS, Sidebar


class FooterTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.initial()
        self.screen = Mock()
        # The panel draws its interior into a subwindow; the tests read one mock.
        self.screen.derwin.return_value = self.screen
        self.screen.getmaxyx.return_value = (38, 28)
        self.store = Mock()
        self.agents = {}
        self.source = Mock(socket="/unused/source.sock", persistent_socket=True)
        self.source.snapshot.side_effect = lambda: (self.agents, "")
        self.display = Mock(sidebar="%0", small=False, keymap=DEFAULT_KEYMAP)
        self.display.snapshot_scope.return_value = contextlib.nullcontext()
        self.display.focused_leaf.side_effect = lambda: (
            self.model.tab["focus"] if self.model.tab else None
        )
        self.sidebar = Sidebar(
            self.screen, self.model, self.store, self.source, self.display, Mock()
        )
        patcher = patch("tmux_workspaces.sidebar.curses.color_pair", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def mouse(self, x, y, buttons=curses.BUTTON1_PRESSED):
        with patch(
            "tmux_workspaces.sidebar.curses.getmouse", return_value=(0, x + 1, y + 1, 0, buttons)
        ):
            self.sidebar.input(curses.KEY_MOUSE)

    def cells(self):
        self.screen.addnstr.reset_mock()
        self.sidebar.last_frame = None
        self.sidebar.draw()
        return {(c.args[0], c.args[1]): c.args[2] for c in self.screen.addnstr.call_args_list}

    def row_text(self, row):
        return "".join(text for (y, _x), text in sorted(self.cells().items()) if y == row).strip()

    def test_the_footer_order_is_context_configure_icons(self):
        cells = self.cells()
        # Interior height 36: context on 32 and 33, Configure… on 34, icons on 35.
        self.assertEqual(cells[32, 1].strip(), "Shell")
        self.assertEqual(cells[34, 1].strip(), "Configure…")
        self.assertEqual(cells[35, 1].strip(), "1")
        self.assertEqual(self.sidebar.footer_rows(), 4)
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 4)

    def test_context_rows_follow_the_focused_pane(self):
        self.agents = {
            "reviewer": {"online": True, "state": "idle", "work": "Checking split resizing."},
        }
        self.model.attach("reviewer", None)
        cells = self.cells()
        self.assertEqual(cells[32, 1].strip(), "reviewer · idle")
        self.assertEqual(cells[33, 1].strip(), "Checking split resizing.")
        # Without a reported task the second row is simply empty.
        self.agents["reviewer"].pop("work")
        cells = self.cells()
        self.assertEqual(cells[32, 1].strip(), "reviewer · idle")
        self.assertEqual(cells[33, 1].strip(), "")
        # An agent the roster no longer lists is offline, never "working".
        self.agents = {}
        self.assertEqual(self.cells()[32, 1].strip(), "reviewer · offline")
        # A shell shows its directory; an empty pane, what it awaits.
        self.model.attach(None, None)
        self.model.pane["cwd"] = "/tmp/work"
        cells = self.cells()
        self.assertEqual((cells[32, 1].strip(), cells[33, 1].strip()), ("Shell", "/tmp/work"))
        self.model.add_tab(empty=True)
        cells = self.cells()
        self.assertEqual(cells[32, 1].strip(), "Empty pane")

    def test_configure_holds_the_infrequent_controls_with_detach_last(self):
        self.sidebar.draw()
        self.mouse(3, 34)
        self.assertEqual(self.sidebar.menu, "configure")
        labels = [label for label, _ in self.sidebar._options({})]
        self.assertEqual(labels[:3], ["Colors…", "Shortcuts", "Refresh viewer…"])
        self.assertEqual(labels[-2:], [RULE, "Detach"])
        # The rule is drawn muted and is never a row the keyboard lands on.
        self.sidebar.draw()
        self.sidebar.input(curses.KEY_END)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "Detach")
        self.sidebar.input(curses.KEY_UP)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "Refresh viewer…")
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "Detach")
        drawn = [c.args[2] for c in self.screen.addnstr.call_args_list]
        self.assertTrue(any(text.startswith("────") for text in drawn))
        self.assertNotIn(RULE, drawn)
        # Leaving is the last thing, and it detaches rather than stopping anything.
        self.sidebar.input("\n")
        self.assertFalse(self.sidebar.running)
        self.display.shells.close.assert_not_called()

    def test_icon_row_switches_on_click_and_opens_options_on_right_click(self):
        first = self.model.space
        self.model.add_workspace("Second")
        second = self.model.space
        self.model.add_workspace("Third")
        third = self.model.space
        self.model.state["selected"] = first["id"]
        cells = self.cells()
        self.assertEqual([cells[35, 1 + 4 * i].strip() for i in range(3)], ["1", "2", "3"])
        self.mouse(6, 35)
        self.assertEqual(self.model.space["id"], second["id"])
        self.mouse(10, 35, curses.BUTTON3_PRESSED)
        self.assertEqual((self.sidebar.menu, self.model.space["id"]), ("workspace", third["id"]))
        self.sidebar.close_menu()
        # Every cell of a slot is its click target.
        for x in (1, 2, 3):
            self.sidebar.draw()
            self.mouse(x, 35)
            self.assertEqual(self.model.space["id"], first["id"])
            self.model.state["selected"] = second["id"]

    def test_icons_are_chosen_from_the_curated_set_and_persist(self):
        self.sidebar.open_menu("workspace")
        dict(self.sidebar._options({}))["Set icon…"]()
        self.assertEqual(self.sidebar.menu, "icon")
        labels = [label for label, _ in self.sidebar._options({})]
        self.assertEqual(len(labels), len(WORKSPACE_ICONS) + 1)
        self.assertEqual(labels[-1], "Number")
        dict(self.sidebar._options({}))[labels[0]]()
        glyph = WORKSPACE_ICONS[0][0]
        self.assertEqual(self.model.space["icon"], glyph)
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.cells()[35, 1].strip(), glyph)
        # The saved form keeps the icon and validates.
        saved = shared_layout(self.model.state)
        self.assertEqual(saved["workspaces"][0]["icon"], glyph)
        validate_state(saved, navigation=False)
        saved["workspaces"][0]["icon"] = "\x1b[31m"
        with self.assertRaises(InvalidLayout):
            validate_state(saved, navigation=False)
        saved["workspaces"][0]["icon"] = "toolong"
        with self.assertRaises(InvalidLayout):
            validate_state(saved, navigation=False)
        # Back to a number.
        self.sidebar.open_menu("icon")
        dict(self.sidebar._options({}))["Number"]()
        self.assertNotIn("icon", self.model.space)
        self.assertEqual(self.cells()[35, 1].strip(), "1")

    def test_a_full_icon_row_ends_in_an_overflow_slot_that_opens_the_list(self):
        for index in range(9):
            self.model.add_workspace(f"Space {index + 2}")
        spaces = self.model.state["workspaces"]
        self.model.state["selected"] = spaces[0]["id"]
        cells = self.cells()
        # Interior width 26: six slots of four cells; five workspaces, then the rest.
        # The frame's own cells sit at column 0; the slots start at column 1.
        row = [text.strip() for (y, x), text in sorted(cells.items()) if y == 35 and x >= 1]
        self.assertEqual(row[:5], ["1", "2", "3", "4", "5"])
        self.assertEqual(row[5], "…")
        self.assertNotIn("6", row)
        self.mouse(22, 35)
        self.assertEqual(self.sidebar.menu, "spaces")
        self.assertEqual(len(self.sidebar._options({})), 10)


if __name__ == "__main__":
    unittest.main()
