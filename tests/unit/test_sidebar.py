import copy
import curses
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap
from tmux_workspaces.model import LayoutConflict, Model, leaves
from tmux_workspaces.sidebar import Sidebar


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.initial()
        self.screen = Mock()
        self.screen.getmaxyx.return_value = (38, 28)
        self.store = Mock()
        self.source = Mock(socket="/unused/source.sock", persistent_socket=True)
        self.source.snapshot.return_value = ({}, "")
        self.display = Mock(sidebar="%0", small=False, keymap=DEFAULT_KEYMAP)
        self.display.focused_leaf.return_value = self.model.tab["focus"]

        def render(tab, focus):
            self.display.focused_leaf.return_value = tab["focus"] if tab else None

        self.display.render.side_effect = render
        self.sidebar = Sidebar(
            self.screen, self.model, self.store, self.source, self.display, Mock()
        )
        colors = patch("tmux_workspaces.sidebar.curses.color_pair", return_value=0)
        colors.start()
        self.addCleanup(colors.stop)

    def mouse(self, x, y, buttons):
        with patch("tmux_workspaces.sidebar.curses.getmouse", return_value=(0, x, y, 0, buttons)):
            self.sidebar.input(curses.KEY_MOUSE)

    def test_help_wraps_effective_keys_and_omits_disabled_actions(self):
        self.sidebar.keymap = Keymap.from_dict(
            {"bindings": {"new-tab": ["M-C-F2", "M-C-F3"], "close-pane": []}}
        )
        self.screen.getmaxyx.return_value = (38, 18)
        options = self.sidebar.shortcut_options(command=False)
        text = " ".join(label for label, _ in options)
        self.assertIn("Alt-Ctrl-F2 / Alt-Ctrl-F3 New tab", text)
        self.assertNotIn("Close pane", text)
        self.assertTrue(all(len(label) <= 16 for label, _ in options))

    def test_layout_conflict_keeps_following_keyboard_input_in_sidebar(self):
        self.store.save.side_effect = LayoutConflict("Changed in another window")
        with self.assertRaises(LayoutConflict):
            self.sidebar.save()
        self.display.render.assert_called_once()
        self.display.select_sidebar.assert_called_with()

    def test_right_click_inactive_tab_renames_target_and_preserves_previous_layout(self):
        first = self.model.tab
        self.model.split("right")
        actual_focus = self.model.pane["id"]
        first["focus"] = leaves(first["tree"])[0]["id"]
        self.display.focused_leaf.return_value = actual_focus
        first_tree = copy.deepcopy(first["tree"])
        self.model.add_tab("Target tab")
        target = self.model.tab
        target_tree = copy.deepcopy(target["tree"])
        self.model.space["selected"] = first["id"]
        self.sidebar.draw()

        self.mouse(3, 4, curses.BUTTON3_PRESSED)

        self.assertEqual(self.sidebar.menu, "tab")
        self.assertEqual(self.model.tab["id"], target["id"])
        self.assertEqual(first["focus"], actual_focus)
        self.assertEqual(first["tree"], first_tree)
        self.assertEqual(target["tree"], target_tree)
        self.display.select_sidebar.assert_called_with()
        self.display.shells.close.assert_not_called()
        dict(self.sidebar._options({}))["Rename tab"]()
        self.assertEqual(self.sidebar.query, "Target tab")
        for char in "Renamed target":
            self.sidebar.input(char)
        self.sidebar.input("\n")
        self.assertEqual(target["name"], "Renamed target")
        self.assertNotEqual(first["name"], "Renamed target")
        self.assertEqual(first["tree"], first_tree)

    def test_right_click_status_row_accepts_click_event_and_does_not_repeat_on_release(self):
        self.sidebar.draw()
        self.mouse(3, 3, curses.BUTTON3_CLICKED)
        self.assertEqual(self.sidebar.menu, "tab")
        self.sidebar.draw()
        self.assertEqual(self.sidebar.context_hits, [])
        self.store.save.reset_mock()
        self.display.select_sidebar.reset_mock()
        self.mouse(3, 3, curses.BUTTON3_RELEASED)
        self.assertEqual(self.sidebar.menu, "tab")
        self.store.save.assert_not_called()
        self.display.select_sidebar.assert_not_called()

    def test_choose_tab_resolves_stale_draw_reference_by_id(self):
        self.model.split("right")
        tab = self.model.tab
        stale = copy.deepcopy(tab)
        self.model.add_tab("Another tab")
        panes = leaves(tab["tree"])
        panes[0]["cwd"] = "/tmp/updated-cwd"
        tab["focus"] = panes[0]["id"]
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        self.sidebar.choose_tab(stale)
        self.assertEqual(self.model.tab["id"], stale["id"])
        self.assertEqual(self.model.tab["focus"], panes[0]["id"])
        self.assertEqual(leaves(self.model.tab["tree"])[0]["cwd"], "/tmp/updated-cwd")
        self.display.shells.close.assert_not_called()

    def test_choose_deleted_tab_leaves_selection_and_terminals_unchanged(self):
        removed = copy.deepcopy(self.model.tab)
        self.model.close_tab()
        self.model.add_tab("Surviving tab")
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        before = copy.deepcopy(self.model.state)
        self.sidebar.choose_tab(removed)
        self.assertEqual(self.model.state, before)
        self.assertTrue(self.sidebar.message)
        self.display.render.assert_not_called()
        self.display.shells.close.assert_not_called()
        self.store.save.assert_not_called()

    def test_deleted_context_targets_do_not_open_survivor_options(self):
        removed_tab = copy.deepcopy(self.model.tab)
        self.model.close_tab()
        self.model.add_tab("Surviving tab")
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        before = copy.deepcopy(self.model.state)
        self.sidebar.context_tab(removed_tab)
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.model.state, before)
        self.display.select_sidebar.assert_not_called()
        removed_space = copy.deepcopy(self.model.space)
        self.model.add_workspace("Surviving workspace")
        self.model.state["workspaces"].pop(0)
        before = copy.deepcopy(self.model.state)
        self.sidebar.context_workspace(removed_space)
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.model.state, before)
        self.display.select_sidebar.assert_not_called()
        self.display.shells.close.assert_not_called()

    def test_right_click_never_triggers_regular_button_and_left_click_still_works(self):
        self.sidebar.draw()
        self.mouse(24, 0, curses.BUTTON3_PRESSED)  # New-tab button has no context action.
        self.assertEqual(len(self.model.space["tabs"]), 1)
        self.assertIsNone(self.sidebar.menu)
        self.mouse(3, 2, curses.BUTTON3_RELEASED)
        self.assertIsNone(self.sidebar.menu)
        self.mouse(24, 0, curses.BUTTON1_PRESSED)
        self.assertEqual(len(self.model.space["tabs"]), 2)

    def test_workspace_button_context_targets_clicked_workspace_and_header_opens_options(self):
        first = self.model.space
        first_tab = self.model.tab
        original = copy.deepcopy(first)
        self.model.add_workspace("Other workspace")
        self.model.add_tab("Other tab")
        target = self.model.space
        target_tree = copy.deepcopy(self.model.tab["tree"])
        self.model.state["selected"] = first["id"]
        self.display.focused_leaf.return_value = first_tab["focus"]
        self.sidebar.draw()

        self.mouse(9, 36, curses.BUTTON3_PRESSED)

        self.assertEqual(self.sidebar.menu, "workspace")
        self.assertEqual(self.model.space["id"], target["id"])
        self.assertEqual(first, original)
        self.assertEqual(self.model.tab["tree"], target_tree)
        self.display.shells.close.assert_not_called()
        dict(self.sidebar._options({}))["Rename workspace"]()
        self.assertEqual(self.sidebar.query, "Other workspace")
        self.sidebar.query = "Renamed workspace"
        self.sidebar.accept_name()
        self.assertEqual(target["name"], "Renamed workspace")
        self.assertEqual(first, original)
        self.sidebar.draw()
        self.mouse(3, 0, curses.BUTTON3_CLICKED)
        self.assertEqual(self.sidebar.menu, "workspace")

    def test_header_adds_tabs_and_footer_keeps_attachment_visible(self):
        self.sidebar.draw()
        before = copy.deepcopy(self.model.state)
        self.mouse(3, 0, curses.BUTTON1_PRESSED)
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.model.state, before)
        labels = [call.args[2].strip() for call in self.screen.addnstr.call_args_list]
        self.assertIn("[ + ]", labels)
        self.assertNotIn("+ Tab", labels)
        self.assertIn("Attach session…", labels)
        self.assertIn("Tab actions…", labels)
        self.assertIn("Workspaces…", labels)
        for x in range(22, 27):
            self.sidebar.draw()
            previous_count = len(self.model.space["tabs"])
            self.mouse(x, 0, curses.BUTTON1_PRESSED)
            self.assertEqual(len(self.model.space["tabs"]), previous_count + 1)

    def test_rectangular_workspace_buttons_are_clickable_across_their_width(self):
        first = self.model.space
        self.model.add_workspace("Second")
        second = self.model.space
        for x in range(1, 6):
            self.sidebar.draw()
            self.mouse(x, 36, curses.BUTTON1_PRESSED)
            self.assertEqual(self.model.space["id"], first["id"])
            self.sidebar.draw()
            self.mouse(x + 6, 36, curses.BUTTON1_PRESSED)
            self.assertEqual(self.model.space["id"], second["id"])
        self.sidebar.draw()
        self.mouse(25, 36, curses.BUTTON1_PRESSED)
        self.assertEqual((self.sidebar.menu, self.sidebar.pending), ("name", "new-workspace"))

    def test_overflow_workspace_arrows_reach_every_workspace_at_narrow_width(self):
        self.screen.getmaxyx.return_value = (38, 18)
        for index in range(1, 12):
            self.model.add_workspace(f"Workspace {index + 1}")
        spaces = self.model.state["workspaces"]
        self.model.state["selected"] = spaces[0]["id"]
        for expected in [*spaces[1:], spaces[0]]:
            self.sidebar.draw()
            self.mouse(11, 36, curses.BUTTON1_PRESSED)
            self.assertEqual(self.model.space["id"], expected["id"])
        self.sidebar.draw()
        self.mouse(2, 36, curses.BUTTON1_PRESSED)
        self.assertEqual(self.model.space["id"], spaces[-1]["id"])
        self.sidebar.draw()
        rectangles = [
            call.args[2].strip()
            for call in self.screen.addnstr.call_args_list
            if call.args[0] == 36 and call.args[1] == 4
        ]
        self.assertIn("[ 12 ]", rectangles)

    def test_indexed_tab_selection_stays_visible_with_shorter_footer(self):
        self.screen.getmaxyx.return_value = (30, 28)
        for index in range(12):
            self.model.add_tab(f"Tab {index + 2}")
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        self.sidebar.action("select-tab-9")
        self.sidebar.draw()
        labels = [call.args[2].strip() for call in self.screen.addnstr.call_args_list]
        self.assertTrue(any("9 Tab 9" in label for label in labels))
        self.assertEqual(self.sidebar.tab_capacity(), 17)
        self.assertEqual(self.sidebar.tab_offset, 0)

    def test_compact_rows_keep_counts_and_click_targets_separate(self):
        first = self.model.tab
        self.model.split("right")
        self.model.add_tab("A very long tab name that must be clipped")
        second = self.model.tab
        self.model.add_tab("Third")
        self.model.space["selected"] = first["id"]
        self.display.focused_leaf.return_value = first["focus"]
        self.sidebar.draw()
        cells = {
            (c.args[0], c.args[1]): c.args[2].strip() for c in self.screen.addnstr.call_args_list
        }
        self.assertEqual(cells[2, 25], "2")
        self.assertEqual(cells[3, 3], "2 panes")
        self.assertTrue(cells[4, 0].endswith("…"))
        self.assertIn("3 Third", cells[5, 0])
        # Counts and the right edge belong to the tab, not an adjacent row.
        self.mouse(25, 4, curses.BUTTON1_PRESSED)
        self.assertEqual(self.model.tab["id"], second["id"])
        self.sidebar.draw()
        self.mouse(3, 4, curses.BUTTON3_PRESSED)  # Detail row moved with selection.
        self.assertEqual(self.sidebar.menu, "tab")
        self.assertEqual(self.model.tab["id"], second["id"])

    def test_scrolled_tabs_do_not_overlap_controls_and_last_tab_is_reachable(self):
        self.screen.getmaxyx.return_value = (20, 28)
        for index in range(30):
            self.model.add_tab(f"Tab {index + 2}")
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        self.sidebar.choose_tab(self.model.tab)
        self.sidebar.draw()
        # No clickable rectangles overlap, including the active detail and scroll row.
        occupied = set()
        for row, left, right, _action in self.sidebar.hits:
            for column in range(left, right):
                self.assertNotIn((row, column), occupied)
                occupied.add((row, column))
        labels = [c.args[2] for c in self.screen.addnstr.call_args_list]
        self.assertTrue(any("31 Tab 31" in text for text in labels))
        self.mouse(3, 13, curses.BUTTON1_PRESSED)
        self.assertEqual(self.sidebar.menu, "agents")

    def test_workspace_actions_create_rename_and_wrap_without_changing_saved_tabs(self):
        first = copy.deepcopy(self.model.space)
        self.sidebar.action("new-workspace")
        self.assertEqual((self.sidebar.menu, self.sidebar.pending), ("name", "new-workspace"))
        self.sidebar.query = "Workspace two"
        self.sidebar.accept_name()
        second = self.model.space
        self.assertEqual(second["tabs"], [])
        self.sidebar.action("rename-workspace")
        self.assertEqual(self.sidebar.query, "Workspace two")
        self.sidebar.query = "Tools"
        self.sidebar.accept_name()
        self.sidebar.action("next-workspace")
        self.assertEqual(self.model.space, first)
        self.sidebar.action("previous-workspace")
        self.assertEqual(self.model.space["id"], second["id"])
        self.assertEqual(self.model.space["name"], "Tools")
        self.assertEqual(self.model.state["workspaces"][0], first)
        self.display.shells.close.assert_not_called()

    def test_sidebar_action_focuses_sidebar_without_redrawing_or_discarding_menu(self):
        self.sidebar.menu, self.sidebar.query = "name", "Unfinished name"
        self.sidebar.action("sidebar")
        self.display.select_sidebar.assert_called_once_with()
        self.display.render.assert_not_called()
        self.assertEqual((self.sidebar.menu, self.sidebar.query), ("name", "Unfinished name"))
        self.sidebar.action("quit")
        self.assertFalse(self.sidebar.running)
        self.display.shells.close.assert_not_called()

    def test_previous_pane_action_wraps_without_replacing_shells_or_layout(self):
        self.model.split("right")
        self.model.split("below")
        panes = leaves(self.model.tab["tree"])
        self.model.tab["focus"] = panes[0]["id"]
        self.display.focused_leaf.return_value = panes[0]["id"]
        tree = copy.deepcopy(self.model.tab["tree"])
        self.sidebar.action("previous-pane")
        self.assertEqual(self.model.tab["focus"], panes[-1]["id"])
        self.display.select.assert_called_once_with(panes[-1]["id"])
        self.assertEqual(self.model.tab["tree"], tree)
        self.display.render.assert_not_called()
        self.display.shells.close.assert_not_called()

    def test_rename_selection_keeps_name_on_enter_and_replaces_or_clears_on_edit(self):
        original = self.model.tab["name"]
        self.sidebar.action("rename-tab")
        self.assertTrue(self.sidebar.replace_name)
        self.sidebar.input("\n")
        self.assertEqual(self.model.tab["name"], original)
        for erase in ("\x15", curses.KEY_BACKSPACE):
            with self.subTest(erase=erase):
                self.sidebar.action("rename-tab")
                self.sidebar.input(erase)
                self.assertEqual(self.sidebar.query, "")
                self.assertFalse(self.sidebar.replace_name)
                self.sidebar.input("N")
                self.assertEqual(self.sidebar.query, "N")
        self.model.space["name"] = "x" * 80
        self.sidebar.action("rename-workspace")
        self.sidebar.input("W")
        self.sidebar.input("2")
        self.assertEqual(self.sidebar.query, "W2")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "W2")
        self.sidebar.action("new-workspace")
        self.assertFalse(self.sidebar.replace_name)
        self.assertEqual(self.sidebar.query, "")


if __name__ == "__main__":
    unittest.main()
