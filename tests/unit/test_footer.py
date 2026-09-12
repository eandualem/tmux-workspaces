"""The lower panel: an optional roster of active agents, one Configure… menu,
the workspace icon row, and the spacing between them and the outline."""

from __future__ import annotations

import contextlib
import curses
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.discovery import Snapshot
from tmux_workspaces.keymap import DEFAULT_KEYMAP
from tmux_workspaces.layout_validation import InvalidLayout, validate_state
from tmux_workspaces.menu import RULE
from tmux_workspaces.model import Model, shared_layout
from tmux_workspaces.sidebar import WORKSPACE_ICONS, Sidebar


def roster(**states: str) -> Snapshot:
    return Snapshot({name: {"name": name, "state": state} for name, state in states.items()})


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
        # A source with a state-reporting provider, currently listing no agents.
        self.source.roster.return_value = roster()
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

    def row_text(self, cells, row):
        # The outline is drawn on the same mock; only the interior's text counts.
        return "".join(text for (y, _x), text in sorted(cells.items()) if y == row).strip("│╭╮╰╯─ ")

    def labels(self):
        return [text.strip() for text in self.cells().values()]

    def options(self):
        return [label for label, _ in self.sidebar._options({})]

    # -- spacing -------------------------------------------------------------

    def test_the_bottom_is_spaced_from_the_outline_and_from_configure(self):
        cells = self.cells()
        # Interior height 36, from the bottom: a blank row, icons, a blank row,
        # Configure…, a row for messages, then the roster ending above it.
        self.assertEqual(self.row_text(cells, 35), "")
        self.assertEqual(cells[34, 1].strip(), "1")
        self.assertEqual(self.row_text(cells, 33), "")
        self.assertEqual(cells[32, 1].strip(), "Configure…")
        self.assertEqual(self.row_text(cells, 31), "")
        self.assertEqual(cells[30, 1].strip(), "No active agents")
        self.assertEqual(cells[29, 1].strip(), "Agents")
        self.assertEqual(self.sidebar.footer_rows(), 5)
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 5 - 2)
        # A message takes the row above Configure…; nothing else moves.
        self.sidebar.message = "Layout changed"
        cells = self.cells()
        self.assertEqual(cells[31, 1].strip(), "Layout changed")
        self.assertEqual(cells[32, 1].strip(), "Configure…")
        self.assertEqual(cells[30, 1].strip(), "No active agents")

    def test_the_heading_shows_the_icon_and_a_blank_row_precedes_the_tabs(self):
        self.model.space["icon"] = "◆"
        self.model.add_tab("Second")
        cells = self.cells()
        self.assertEqual(cells[0, 1], "◆")
        self.assertTrue(cells[0, 3].startswith("Workspace 1"))
        self.assertEqual(cells[0, 23].strip(), "▾")
        self.assertEqual(self.row_text(cells, 1), "")
        self.assertEqual(cells[2, 1].strip(), "tabs")
        self.assertEqual(cells[2, 21].strip(), "+")
        self.assertIn("1 " + self.model.space["tabs"][0]["name"], cells[3, 0])
        self.assertIn("2 Second", cells[4, 0])
        # A long name truncates before the chevron, icon included.
        self.model.space["name"] = "A workspace with a very long name"
        cells = self.cells()
        self.assertEqual(cells[0, 1], "◆")
        self.assertTrue(cells[0, 3].endswith("…"))
        self.assertLessEqual(3 + len(cells[0, 3]), 23)
        self.assertEqual(cells[0, 23].strip(), "▾")

    def test_no_summary_of_the_focused_pane_is_drawn(self):
        self.agents = {"reviewer": {"online": True, "state": "idle", "work": "Checking."}}
        self.model.attach("reviewer", None)
        self.model.pane["cwd"] = "/tmp/work"
        labels = self.labels()
        for gone in ("reviewer · idle", "Checking.", "Shell", "/tmp/work", "Empty pane", "shells"):
            self.assertNotIn(gone, labels)
        self.model.add_tab(empty=True)
        self.assertNotIn("Empty pane", self.labels())

    # -- the roster ----------------------------------------------------------

    def test_roster_rows_carry_a_symbol_slot_and_a_name_in_a_stable_order(self):
        self.source.roster.return_value = roster(
            zed="busy", Alpha="idle", mid="waiting_for_human", gone="offline", odd="weird"
        )
        cells = self.cells()
        rows = [(cells[r, 1], cells[r, 3]) for r in range(27, 31)]
        self.assertEqual(rows, [("○", "Alpha"), ("!", "mid"), ("?", "odd"), ("▶", "zed")])
        self.assertEqual(cells[26, 1].strip(), "Agents")
        self.assertNotIn("gone", self.labels())
        # Offline agents take no row; the label sits right above the first agent.
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 5 - 5)
        # States change; positions do not.
        self.source.roster.return_value = roster(
            zed="idle", Alpha="busy", mid="idle", gone="idle", odd="starting"
        )
        cells = self.cells()
        rows = [(cells[r, 1], cells[r, 3]) for r in range(26, 31)]
        self.assertEqual(
            rows, [("▶", "Alpha"), ("○", "gone"), ("○", "mid"), ("▶", "odd"), ("○", "zed")]
        )

    def test_symbols_have_plain_fallbacks_and_states_have_names(self):
        self.assertEqual(self.sidebar.symbol("busy"), "▶")
        self.assertEqual(self.sidebar.symbol("blocked"), "!")
        self.sidebar.unicode = False
        self.assertEqual(self.sidebar.symbol("busy"), ">")
        self.assertEqual(self.sidebar.symbol("idle"), "o")
        self.assertEqual(self.sidebar.symbol("waiting_for_human"), "!")
        self.assertEqual(self.sidebar.symbol("something_new"), "?")
        self.assertEqual(self.sidebar.state_name("waiting_for_human"), "waiting for you")
        self.assertEqual(self.sidebar.state_name("some_thing"), "some thing")

    def test_a_stale_roster_says_it_is_unavailable_instead_of_showing_old_states(self):
        self.source.roster.return_value = Snapshot(
            {"reviewer": {"name": "reviewer", "state": "idle"}},
            error="Backbone unavailable; states stale",
            stale=True,
        )
        cells = self.cells()
        self.assertEqual(cells[29, 1].strip(), "Agents")
        self.assertEqual(cells[30, 1].strip(), "Roster unavailable")
        self.assertNotIn("reviewer", self.labels())
        self.sidebar.open_menu("status")
        options = self.options()
        # The reason wraps to the panel's width; the legend still follows the rule.
        self.assertEqual(
            options[:3], ["Roster unavailable", "Backbone unavailable;", "states stale"]
        )
        self.assertEqual(options[3], RULE)
        self.assertEqual(len(options), 4 + 4)

    def test_the_status_menu_spells_states_out_and_ends_with_the_legend(self):
        self.source.roster.return_value = roster(
            builder="busy", manager="waiting_for_human", tester="idle", notes="offline"
        )
        self.cells()
        # Clicking an agent row opens the details (label on 27, agents 28–30).
        self.mouse(5, 28)
        self.assertEqual(self.sidebar.menu, "status")
        options = self.options()
        self.assertEqual(
            options[:4],
            ["▶ builder · working", "! manager · waiting for you", "○ tester · idle", "1 offline"],
        )
        self.assertEqual(options[4], RULE)
        self.assertEqual(
            options[5:],
            ["▶ working", "! needs you: waiting or blocked", "○ idle", "? state unknown"],
        )
        # The keyboard reaches the legend; the rule is skipped.
        self.sidebar.draw()
        for _ in range(4):
            self.sidebar.input(curses.KEY_DOWN)
            self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "▶ working")

    def test_the_roster_is_bounded_scrolls_and_shrinks_before_the_tabs_do(self):
        names = [f"agent{index:02d}" for index in range(9)]
        self.source.roster.return_value = roster(**dict.fromkeys(names, "idle"))
        cells = self.cells()
        # Six rows at most, a total and scroll arrows on the label row.
        self.assertEqual(cells[24, 1].strip(), "Agents")
        self.assertEqual(cells[24, 19].strip(), "9")
        self.assertEqual((cells[24, 21].strip(), cells[24, 23].strip()), ("↑", "↓"))
        self.assertEqual([cells[r, 3] for r in range(25, 31)], names[:6])
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 5 - 7)
        self.mouse(23, 24)
        self.mouse(23, 24)
        self.mouse(23, 24)
        cells = self.cells()
        self.assertEqual([cells[r, 3] for r in range(25, 31)], names[3:])
        # The wheel over the rows scrolls them back; over the tabs it does not.
        self.mouse(5, 27, curses.BUTTON4_PRESSED)
        self.assertEqual(self.sidebar.roster_offset, 2)
        self.mouse(5, 3, curses.BUTTON4_PRESSED)
        self.assertEqual(self.sidebar.roster_offset, 2)
        # Shorter windows give rows back to the tabs first: four tab rows stay.
        for index in range(10):
            self.model.add_tab(f"Tab {index + 2}")
        self.screen.getmaxyx.return_value = (20, 28)
        cells = self.cells()
        self.assertEqual(self.sidebar.roster_rows(), 6)
        self.assertEqual(self.sidebar.tab_capacity(), 4)
        self.assertEqual(cells[7, 1].strip(), "Agents")
        self.assertEqual(cells[14, 1].strip(), "Configure…")
        self.assertEqual(cells[16, 1].strip(), "1")
        self.screen.getmaxyx.return_value = (16, 28)
        cells = self.cells()
        self.assertEqual(self.sidebar.roster_rows(), 2)
        self.assertEqual(self.sidebar.tab_capacity(), 4)
        self.assertEqual(cells[7, 1].strip(), "Agents")
        self.assertEqual(cells[10, 1].strip(), "Configure…")
        occupied = set()
        for row, left, right, _action in self.sidebar.hits:
            for column in range(left, right):
                self.assertNotIn((row, column), occupied)
                occupied.add((row, column))

    def test_configure_toggles_the_roster_and_the_choice_is_saved(self):
        self.source.roster.return_value = roster(builder="busy")
        self.assertIn("builder", self.labels())
        self.sidebar.open_menu("configure")
        options = self.options()
        self.assertEqual(options[:3], ["Edit theme…", "Edit shortcuts…", "View shortcuts…"])
        self.assertEqual(options[4:6], ["[x] Show agent status", "Agent status…"])
        self.assertEqual(options[-2:], [RULE, "Detach"])
        dict(self.sidebar._options({}))["[x] Show agent status"]()
        self.assertIs(self.model.state["show_agents"], False)
        self.store.save.assert_called_with(self.model)
        # The menu stays open on the same row and shows the new setting.
        self.assertEqual(self.sidebar.menu, "configure")
        self.assertIn("[ ] Show agent status", self.options())
        self.sidebar.close_menu()
        labels = self.labels()
        for hidden in ("Agents", "builder", "No active agents"):
            self.assertNotIn(hidden, labels)
        self.assertEqual(self.sidebar.roster_rows(), 0)
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 5)
        cells = self.cells()
        self.assertEqual(cells[32, 1].strip(), "Configure…")
        self.assertEqual(cells[34, 1].strip(), "1")
        # The preference travels with the shared layout and is validated.
        saved = shared_layout(self.model.state)
        self.assertIs(saved["show_agents"], False)
        validate_state(saved, navigation=False)
        saved["show_agents"] = "no"
        with self.assertRaises(InvalidLayout):
            validate_state(saved, navigation=False)
        # Back on, through the same entry.
        self.sidebar.open_menu("configure")
        dict(self.sidebar._options({}))["[ ] Show agent status"]()
        self.assertIs(self.model.state["show_agents"], True)
        self.sidebar.close_menu()
        self.assertIn("builder", self.labels())

    def test_without_a_state_reporting_provider_there_is_no_roster_or_toggle(self):
        self.source.roster.return_value = None
        labels = self.labels()
        for absent in ("Agents", "No active agents", "Roster unavailable"):
            self.assertNotIn(absent, labels)
        self.assertEqual(self.sidebar.tab_capacity(), 36 - 3 - 5)
        self.sidebar.open_menu("configure")
        self.assertEqual(
            self.options(),
            [
                "Edit theme…",
                "Edit shortcuts…",
                "View shortcuts…",
                "Refresh viewer…",
                RULE,
                "Detach",
            ],
        )

    def test_detach_is_last_and_reached_by_the_keyboard_past_the_rule(self):
        self.cells()
        self.mouse(3, 32)
        self.assertEqual(self.sidebar.menu, "configure")
        self.sidebar.draw()
        self.sidebar.input(curses.KEY_END)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "Detach")
        self.sidebar.input(curses.KEY_UP)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "Agent status…")
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

    # -- the icon row --------------------------------------------------------

    def test_icon_row_switches_on_click_and_opens_options_on_right_click(self):
        first = self.model.space
        self.model.add_workspace("Second")
        second = self.model.space
        self.model.add_workspace("Third")
        third = self.model.space
        self.model.state["selected"] = first["id"]
        cells = self.cells()
        self.assertEqual([cells[34, 1 + 4 * i].strip() for i in range(3)], ["1", "2", "3"])
        self.mouse(6, 34)
        self.assertEqual(self.model.space["id"], second["id"])
        self.mouse(10, 34, curses.BUTTON3_PRESSED)
        self.assertEqual((self.sidebar.menu, self.model.space["id"]), ("workspace", third["id"]))
        self.sidebar.close_menu()
        # Every cell of a slot is its click target.
        for x in (1, 2, 3):
            self.sidebar.draw()
            self.mouse(x, 34)
            self.assertEqual(self.model.space["id"], first["id"])
            self.model.state["selected"] = second["id"]

    def test_icons_are_chosen_from_the_curated_set_and_shown_in_the_heading(self):
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
        cells = self.cells()
        self.assertEqual(cells[34, 1].strip(), glyph)
        self.assertEqual(cells[0, 1], glyph)
        # The saved form keeps the icon and validates.
        saved = shared_layout(self.model.state)
        self.assertEqual(saved["workspaces"][0]["icon"], glyph)
        validate_state(saved, navigation=False)
        saved["workspaces"][0]["icon"] = "\x1b[31m"
        with self.assertRaises(InvalidLayout):
            validate_state(saved, navigation=False)
        # Back to a number; the heading is the bare name again.
        self.sidebar.open_menu("icon")
        dict(self.sidebar._options({}))["Number"]()
        self.assertNotIn("icon", self.model.space)
        cells = self.cells()
        self.assertEqual(cells[34, 1].strip(), "1")
        self.assertTrue(cells[0, 1].startswith("Workspace 1"))

    def test_a_full_icon_row_ends_in_an_overflow_slot_that_opens_the_list(self):
        for index in range(9):
            self.model.add_workspace(f"Space {index + 2}")
        spaces = self.model.state["workspaces"]
        self.model.state["selected"] = spaces[0]["id"]
        cells = self.cells()
        # Interior width 26: six slots of four cells; five workspaces, then the rest.
        row = [text.strip() for (y, x), text in sorted(cells.items()) if y == 34 and x >= 1]
        self.assertEqual(row[:5], ["1", "2", "3", "4", "5"])
        self.assertEqual(row[5], "…")
        self.assertNotIn("6", row)
        self.mouse(22, 34)
        self.assertEqual(self.sidebar.menu, "spaces")
        self.assertEqual(len(self.sidebar._options({})), 10)


if __name__ == "__main__":
    unittest.main()
