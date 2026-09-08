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


class KeyboardMenuTests(SidebarTests):
    """Every chooser and options menu is selectable and activatable by keyboard."""

    def sessions(self, *names):
        self.source.snapshot.return_value = (
            {name: {"online": True, "state": "ready"} for name in names},
            "",
        )

    def labels(self):
        self.sidebar.draw()
        return [text for text, _ in self.sidebar.options]

    def select(self, label):
        for _ in range(self.labels().index(label)):
            self.sidebar.input(curses.KEY_DOWN)
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], label)

    def test_filter_then_arrow_and_enter_attaches_the_selected_session(self):
        self.sessions("builder", "bureau", "manager")
        pane = self.model.pane["id"]
        self.sidebar.action("attach")
        self.assertEqual(self.sidebar.menu, "agents")
        self.sidebar.input("b")
        self.assertEqual(self.labels(), ["builder", "bureau"])
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.input("\n")
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.model.pane["id"], pane)
        self.assertEqual(self.model.pane["agent"], "bureau")
        self.assertEqual(self.model.pane["source_socket"], "/unused/source.sock")
        self.display.shells.close.assert_not_called()

    def test_control_aliases_and_carriage_return_drive_the_chooser_too(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.sidebar.input("\x0e")
        self.assertEqual(self.sidebar.selected, 1)
        self.sidebar.input("\x10")
        self.assertEqual(self.sidebar.selected, 0)
        self.sidebar.input("\r")
        self.assertEqual(self.model.pane["agent"], "builder")

    def test_typing_still_filters_and_never_moves_the_selection_by_itself(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.sidebar.input(curses.KEY_DOWN)
        self.assertEqual(self.sidebar.selected, 1)
        self.sidebar.input("m")
        self.assertEqual((self.sidebar.query, self.sidebar.selected), ("m", 0))
        self.assertEqual(self.labels(), ["manager"])
        self.sidebar.input(curses.KEY_BACKSPACE)
        self.assertEqual((self.sidebar.query, self.sidebar.selected), ("", 0))

    def test_enter_without_a_matching_session_leaves_the_chooser_open(self):
        self.sessions("builder")
        self.sidebar.action("attach")
        for char in "zzz":
            self.sidebar.input(char)
        self.assertEqual(self.labels(), [])
        self.sidebar.input("\n")
        self.assertEqual(self.sidebar.menu, "agents")
        self.assertIsNone(self.model.pane["agent"])
        self.display.shells.close.assert_not_called()

    def test_session_appearing_before_the_selected_one_does_not_redirect_attach(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.select("manager")
        self.sessions("archivist", "builder", "manager")
        self.assertEqual(self.labels(), ["archivist", "builder", "manager"])
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "manager")

    def test_resize_key_keeps_the_open_chooser_and_its_selection(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.select("manager")
        self.sidebar.input(curses.KEY_RESIZE)
        self.assertEqual((self.sidebar.menu, self.sidebar.query), ("agents", ""))
        self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "manager")

    def test_scrolled_chooser_activates_a_row_that_is_actually_visible(self):
        self.screen.getmaxyx.return_value = (16, 28)
        self.sessions(*[f"session-{index:02d}" for index in range(20)])
        self.sidebar.action("attach")
        self.sidebar.scroll(6)
        self.sidebar.draw()
        visible = [text for text, _ in self.sidebar.options][
            self.sidebar.offset : self.sidebar.offset + 8
        ]
        active = self.sidebar.options[self.sidebar.selected][0]
        self.assertIn(active, visible)
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], active)

    def test_moving_the_selection_repaints_the_frame(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.sidebar.draw()
        painted = len(self.screen.addnstr.call_args_list)
        self.sidebar.draw()
        self.assertEqual(len(self.screen.addnstr.call_args_list), painted)
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.draw()
        self.assertGreater(len(self.screen.addnstr.call_args_list), painted)

    def test_tab_options_open_by_keyboard_and_return_the_pane_to_its_shell(self):
        self.model.attach("manager", "/unused/source.sock")
        self.sidebar.action("tab-options")
        self.assertEqual(self.sidebar.menu, "tab")
        self.select("Return pane to shell")
        self.sidebar.input("\n")
        self.assertIsNone(self.model.pane["agent"])
        self.assertNotIn("source_socket", self.model.pane)
        self.assertIsNone(self.sidebar.menu)
        self.display.shells.close.assert_not_called()

    def test_return_to_shell_refuses_a_pane_a_peer_changed(self):
        self.model.attach("manager", "/unused/source.sock")
        pane = self.model.pane
        self.sidebar.action("tab-options")
        self.assertEqual(self.sidebar.attach_target[1], pane["id"])
        self.select("Return pane to shell")

        def peer(model):
            model.pane["agent"] = "peer session"

        self.store.refresh.side_effect = peer
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "peer session")
        self.assertIn("Pane changed", self.sidebar.message)
        self.display.select_sidebar.assert_called_with()
        self.display.shells.close.assert_not_called()

    def test_tab_menu_reaches_reordering_and_transfer_without_a_mouse(self):
        first = self.model.tab
        self.model.add_tab("Second tab")
        second = self.model.tab
        self.display.focused_leaf.return_value = second["focus"]
        self.sidebar.action("tab-options")
        self.select("Move tab up")
        self.sidebar.input("\n")
        self.assertEqual(
            [tab["id"] for tab in self.model.space["tabs"]], [second["id"], first["id"]]
        )
        self.model.add_workspace("Target workspace")
        target = self.model.space
        self.model.state["selected"] = self.model.state["workspaces"][0]["id"]
        self.sidebar.action("tab-options")
        self.select("Move to workspace")
        self.sidebar.input("\n")
        self.assertEqual(self.sidebar.menu, "move")
        self.select("Target workspace")
        self.sidebar.input("\n")
        self.assertEqual([tab["id"] for tab in target["tabs"]], [second["id"]])
        self.assertEqual(self.model.space["id"], target["id"])

    def test_workspace_options_open_by_keyboard_and_delete_an_empty_workspace(self):
        first = self.model.space
        self.model.add_workspace("Scratch")
        scratch = self.model.space
        self.sidebar.action("workspace-options")
        self.assertEqual(self.sidebar.menu, "workspace")
        self.select("Delete empty workspace")
        self.sidebar.input("\n")
        self.assertEqual([space["id"] for space in self.model.state["workspaces"]], [first["id"]])
        self.assertNotIn(scratch["id"], [space["id"] for space in self.model.state["workspaces"]])
        self.display.shells.close.assert_not_called()

    def test_workspace_switcher_selects_by_keyboard_and_escape_closes_the_menu(self):
        first = self.model.space
        self.model.add_workspace("Research")
        self.model.state["selected"] = first["id"]
        self.sidebar.action("workspaces")
        self.select("Research")
        self.sidebar.input("\x1b")
        self.assertIsNone(self.sidebar.menu)
        self.assertEqual(self.model.space["id"], first["id"])
        self.sidebar.action("workspaces")
        self.select("Research")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "Research")
        self.assertIsNone(self.sidebar.menu)

    def test_repeated_workspace_names_activate_the_intended_workspace(self):
        first = self.model.space
        self.model.add_workspace("Shared")
        self.model.add_workspace("Shared")
        third = self.model.space
        self.model.state["selected"] = first["id"]
        self.sidebar.action("workspaces")
        self.assertEqual(self.labels(), [first["name"], "Shared", "Shared"])
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["id"], third["id"])

    def test_rows_added_to_the_menu_source_stay_selectable(self):
        self.sidebar.open_menu("spaces")
        source, chosen = self.sidebar._options, []
        self.sidebar._options = lambda agents: [
            *source(agents),
            ("Extra row", lambda: chosen.append("extra")),
        ]
        self.select("Extra row")
        self.sidebar.input("\n")
        self.assertEqual(chosen, ["extra"])

    def test_enter_on_a_drawn_list_attaches_its_first_row(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.assertEqual(self.labels(), ["builder", "manager"])
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "builder")

    def test_enter_on_drawn_filtered_results_attaches_the_first_match(self):
        self.sessions("builder", "manager", "researcher")
        self.sidebar.action("attach")
        for char in "re":
            self.sidebar.input(char)
        self.assertEqual(self.labels(), ["researcher"])
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "researcher")

    def test_session_arriving_after_an_empty_result_is_not_attached_by_that_enter(self):
        self.sessions("builder")
        self.sidebar.action("attach")
        for char in "note":
            self.sidebar.input(char)
        self.assertEqual(self.labels(), [])
        # The chooser is showing no results when a matching session appears.
        self.sessions("builder", "notebook")
        self.sidebar.input("\n")
        self.assertIsNone(self.model.pane["agent"])
        self.assertEqual(self.sidebar.menu, "agents")
        self.assertEqual(self.labels(), ["notebook"])
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "notebook")

    def test_a_menu_too_small_to_draw_activates_nothing_until_it_fits_again(self):
        self.sessions("unseen")
        self.sidebar.action("attach")
        self.screen.getmaxyx.return_value = (15, 28)
        self.sidebar.draw()
        drawn = [call.args[2].strip() for call in self.screen.addnstr.call_args_list]
        self.assertEqual(drawn, ["Enlarge terminal", "Exit viewer"])
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.input("\n")
        self.assertIsNone(self.model.pane["agent"])
        self.assertEqual(self.sidebar.menu, "agents")
        # The chooser is still open, so restoring the size restores activation.
        self.screen.getmaxyx.return_value = (38, 28)
        self.assertEqual(self.labels(), ["unseen"])
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "unseen")

    def test_shrinking_below_the_minimum_disarms_a_row_chosen_while_visible(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.select("manager")
        for size in ((15, 28), (38, 17)):
            with self.subTest(size=size):
                self.screen.getmaxyx.return_value = size
                self.sidebar.draw()
                self.sidebar.input(curses.KEY_UP)
                self.sidebar.input("\n")
                self.assertIsNone(self.model.pane["agent"])
                self.screen.getmaxyx.return_value = (38, 28)
                self.sidebar.draw()
                self.assertEqual(self.sidebar.options[self.sidebar.selected][0], "manager")
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "manager")

    def test_a_session_that_disappears_refuses_to_attach_its_neighbour(self):
        self.sessions("builder", "manager")
        self.sidebar.action("attach")
        self.select("manager")
        self.sessions("builder", "reviewer")
        self.sidebar.input("\n")
        self.assertIsNone(self.model.pane["agent"])
        self.assertEqual(self.sidebar.menu, "agents")
        self.assertIn("choose again", self.sidebar.message)
        self.sidebar.input(curses.KEY_DOWN)
        self.sidebar.input("\n")
        self.assertEqual(self.model.pane["agent"], "reviewer")

    def test_focus_moving_to_a_pane_closes_the_menu_instead_of_stealing_input(self):
        self.sessions("builder")
        self.sidebar.action("attach")
        self.sidebar.close_menu()
        self.assertIsNone(self.sidebar.menu)
        self.assertIsNone(self.sidebar.attach_target)
        self.assertEqual(self.sidebar.options, [])
        self.display.render.assert_not_called()
        self.display.select_sidebar.assert_called_once_with()

    def test_shortcut_help_activates_the_selected_action(self):
        self.sidebar.open_menu("shortcuts")
        self.select(next(text for text in self.labels() if text.endswith("New tab")))
        self.sidebar.input("\n")
        self.assertEqual(len(self.model.space["tabs"]), 2)


if __name__ == "__main__":
    unittest.main()
