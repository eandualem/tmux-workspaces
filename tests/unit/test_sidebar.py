import contextlib
import copy
import curses
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from tmux_workspaces.keymap import DEFAULT_KEYMAP, Keymap
from tmux_workspaces.model import LayoutConflict, Model, leaves
from tmux_workspaces.sidebar import Sidebar, terminal_text
from tmux_workspaces.theme import DEFAULT_THEME


class FakeRelaunch:
    """Stand in for the launcher's request without closing anything."""

    def __init__(self, problem=None, lines=("Restarts this viewer only.",)):
        self.problem, self.lines = problem, lines
        self.manual_reopen = False
        self.command = None
        self.requested = False
        self.submitted = []

    def notes(self):
        return self.lines

    def check(self):
        return self.problem

    def manual_command(self):
        return self.command

    def submit(self, navigation):
        if self.requested:
            return False
        self.requested = True
        self.submitted.append(navigation)
        return True


class FakeActions:
    """Queued shortcut senders, acknowledged exactly like the real receiver."""

    def __init__(self, queued=()):
        self.queued = list(queued)
        self.acknowledged = []

    def pending(self):
        while self.queued:
            action = self.queued.pop(0)
            try:
                yield action
            finally:
                self.acknowledged.append(action)


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.initial()
        self.screen = Mock()
        # The panel draws its interior into a subwindow; the tests read one mock.
        self.screen.derwin.return_value = self.screen
        self.screen.getmaxyx.return_value = (38, 28)
        self.store = Mock()
        self.source = Mock(socket="/unused/source.sock", persistent_socket=True)
        self.source.snapshot.return_value = ({}, "")
        self.display = Mock(sidebar="%0", small=False, keymap=DEFAULT_KEYMAP)
        self.display.snapshot_scope.return_value = contextlib.nullcontext()
        self.display.focused_leaf.return_value = self.model.tab["focus"]

        def render(tab, focus):
            self.display.focused_leaf.return_value = tab["focus"] if tab else None

        self.display.render.side_effect = render
        self.relaunch = FakeRelaunch()
        self.sidebar = Sidebar(
            self.screen,
            self.model,
            self.store,
            self.source,
            self.display,
            Mock(),
            relaunch=self.relaunch,
        )
        colors = patch("tmux_workspaces.sidebar.curses.color_pair", return_value=0)
        colors.start()
        self.addCleanup(colors.stop)

    def mouse(self, x, y, buttons):
        # Coordinates are the panel interior's; the outline around it is one cell.
        with patch(
            "tmux_workspaces.sidebar.curses.getmouse", return_value=(0, x + 1, y + 1, 0, buttons)
        ):
            self.sidebar.input(curses.KEY_MOUSE)

    def test_layout_conflict_keeps_following_keyboard_input_in_sidebar(self):
        self.store.save.side_effect = LayoutConflict("Changed in another window")
        with self.assertRaises(LayoutConflict):
            self.sidebar.save()
        self.display.render.assert_called_once()
        self.display.select_sidebar.assert_called_with()

    def test_ascii_frames_menus_and_user_names_remain_drawable_and_clickable(self):
        self.sidebar.encoding = "ascii"
        self.model.space["name"] = "Café 界"
        self.model.space["icon"] = "◆"
        self.model.tab["name"] = "Résumé 界"
        before = copy.deepcopy(self.model.state)

        def require_ascii(_row, _column, text, *_args):
            text.encode("ascii")

        self.screen.addnstr.side_effect = require_ascii
        self.screen.insstr.side_effect = require_ascii
        self.sidebar.draw()
        drawn = [call.args[2] for call in self.screen.addnstr.call_args_list]
        self.assertIn("+" + "-" * 26, drawn)
        self.assertIn("Caf? ??", drawn)
        self.assertTrue(any("R?sum? ??" in text for text in drawn))
        self.assertEqual(self.model.state, before)
        configure = next(
            call.args
            for call in self.screen.addnstr.call_args_list
            if call.args[2].strip() == "Configure~"
        )
        row, start = configure[:2]
        self.mouse(start, row, curses.BUTTON1_PRESSED)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.menu, "configure")
        self.sidebar.open_menu("icon")
        self.sidebar.draw()
        self.sidebar.open_menu("tab")
        self.sidebar.draw()
        self.assertEqual(self.model.state, before)

    def test_terminal_text_preserves_supported_characters_and_display_width(self):
        text = "Café 界 ◆ ─"
        self.assertEqual(terminal_text(text, "utf-8"), text)
        self.assertEqual(terminal_text(text, "latin-1"), "Café ?? ? -")
        self.assertEqual(terminal_text("╭─│", "cp437"), "+─│")
        self.assertEqual(terminal_text("e\u0301界", "ascii"), "e??")

    def test_window_encoding_takes_precedence_over_locale(self):
        self.screen.encoding = "utf-8"
        with patch("tmux_workspaces.sidebar.locale.getpreferredencoding", return_value="ascii"):
            sidebar = Sidebar(
                self.screen, self.model, self.store, self.source, self.display, Mock()
            )
        self.assertEqual(sidebar.encoding, "utf-8")
        self.assertEqual(sidebar.glyph("▶"), "▶")

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
        self.mouse(23, 2, curses.BUTTON3_PRESSED)  # New-tab button has no context action.
        self.assertEqual(len(self.model.space["tabs"]), 1)
        self.assertIsNone(self.sidebar.menu)
        self.mouse(3, 2, curses.BUTTON3_RELEASED)
        self.assertIsNone(self.sidebar.menu)
        self.mouse(23, 2, curses.BUTTON1_PRESSED)
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

        # The chevron at the heading's right end is the workspace chooser.
        self.mouse(24, 0, curses.BUTTON1_PRESSED)
        self.assertEqual(self.sidebar.menu, "workspace")
        dict(self.sidebar._options({}))["Switch workspace"]()
        self.assertEqual(self.sidebar.menu, "spaces")
        dict(self.sidebar._options({}))["Other workspace"]()

        self.assertEqual(self.model.space["id"], target["id"])
        self.assertEqual(first, original)
        self.assertEqual(self.model.tab["tree"], target_tree)
        self.display.shells.close.assert_not_called()
        self.sidebar.draw()
        self.mouse(24, 0, curses.BUTTON3_PRESSED)
        self.assertEqual(self.sidebar.menu, "workspace")
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
        self.assertIn("+", labels)
        self.assertNotIn("+ Tab", labels)
        self.assertNotIn("Attach session…", labels)
        self.assertNotIn("Tab actions…", labels)
        self.assertNotIn("Workspaces…", labels)
        self.assertNotIn("Layouts saved", labels)
        # Infrequent controls live behind Configure…, with leaving last after a rule.
        self.assertIn("Configure…", labels)
        for hidden in ("Shortcuts", "Colors…", "Detach"):
            self.assertNotIn(hidden, labels)
        self.sidebar.open_menu("configure")
        options = [label for label, _ in self.sidebar._options({})]
        self.assertEqual(options[:3], ["Edit theme…", "Edit shortcuts…", "View shortcuts…"])
        self.assertEqual(options[-1], "Detach")
        self.sidebar.close_menu()
        self.sidebar.draw()
        for x in range(21, 25):
            self.sidebar.draw()
            previous_count = len(self.model.space["tabs"])
            self.mouse(x, 2, curses.BUTTON1_PRESSED)
            self.assertEqual(len(self.model.space["tabs"]), previous_count + 1)

    def test_json_popup_guards_input_and_drains_queued_actions_on_cancel(self):
        self.sidebar.actions = FakeActions(["close-tab", "new-tab"])
        popup = Mock(kind="colors")
        popup.poll.return_value = {"saved": False}
        before = copy.deepcopy(self.model.state)
        with patch("tmux_workspaces.sidebar.ConfigPopup", return_value=popup):
            self.sidebar.open_config_editor("colors")
        self.assertEqual(self.sidebar.menu, "json-settings")
        self.sidebar.action("close-tab")
        self.sidebar.input("t")
        self.assertEqual(self.model.state, before)
        self.assertTrue(self.sidebar.finish_config_editor())
        self.assertIsNone(self.sidebar.config_popup)
        self.assertEqual(self.model.state, before)
        self.assertEqual(self.sidebar.actions.acknowledged, ["close-tab", "new-tab"])
        self.display.shells.close.assert_not_called()

    def test_shortcut_reference_uses_running_keymap_without_a_config_file(self):
        self.sidebar.keymap_path = None
        self.sidebar.actions = FakeActions(["new-tab"])
        popup = Mock(kind="reference")
        popup.poll.return_value = {"saved": False}
        with patch("tmux_workspaces.sidebar.ConfigPopup", return_value=popup) as create:
            self.sidebar.open_menu("configure")
            options = dict(self.sidebar._options({}))
            self.assertNotIn("Colors…", options)
            self.assertNotIn("Shortcuts", options)
            self.assertFalse(any("JSON" in label for label in options))
            options["View shortcuts…"]()
        create.assert_called_once_with(self.display, "reference", None, keymap=self.sidebar.keymap)
        self.sidebar.action("new-tab")
        self.sidebar.finish_config_editor()
        self.assertEqual(len(self.model.space["tabs"]), 1)
        self.assertIsNone(self.sidebar.menu)

    def test_json_popup_launch_failure_returns_to_viewer_with_a_visible_error(self):
        with (
            patch("tmux_workspaces.sidebar.ConfigPopup", side_effect=OSError("could not spawn")),
            patch.object(self.sidebar, "save", side_effect=LayoutConflict("concurrent edit")),
        ):
            self.sidebar.open_config_editor("colors")
        self.assertIsNone(self.sidebar.config_popup)
        self.assertIsNone(self.sidebar.menu)
        self.assertIn("Could not open settings editor", self.sidebar.status_text())
        self.display.shells.close.assert_not_called()

    def test_json_popup_close_never_writes_layout_and_saved_colors_still_apply(self):
        self.sidebar.actions = FakeActions()
        for result in ({"saved": False}, {"error": "popup failed"}, {"saved": True}):
            popup = Mock(kind="colors")
            popup.poll.return_value = result
            self.sidebar.config_popup = popup
            self.sidebar.menu = "json-settings"
            with (
                patch.object(self.sidebar, "save", side_effect=LayoutConflict("concurrent edit")),
                patch("tmux_workspaces.sidebar.load_theme") as load,
                patch.object(self.sidebar, "install") as install,
            ):
                load.return_value = Mock(diagnostic=None, theme=DEFAULT_THEME)
                self.assertTrue(self.sidebar.finish_config_editor())
                if result.get("saved"):
                    install.assert_called_once_with(DEFAULT_THEME)
                    self.assertEqual(self.sidebar.message, "Colors saved and applied")
                else:
                    install.assert_not_called()
            self.assertIsNone(self.sidebar.menu)
            self.assertIsNone(self.sidebar.config_popup)
            popup.close.assert_called_once()

    def test_json_shortcuts_require_a_selected_file_and_offer_refresh_after_save(self):
        with patch("tmux_workspaces.sidebar.ConfigPopup") as create:
            self.sidebar.open_config_editor("shortcuts")
            create.assert_not_called()
            self.assertIn("No keymap file", self.sidebar.menu_message)
        self.sidebar.keymap_path = Path("/unused/keymap.toml")
        self.sidebar.actions = FakeActions()
        popup = Mock(kind="shortcuts")
        popup.poll.return_value = {"saved": True}
        with patch("tmux_workspaces.sidebar.ConfigPopup", return_value=popup):
            self.sidebar.open_config_editor("shortcuts")
        with patch.object(self.sidebar, "save", side_effect=LayoutConflict("concurrent edit")):
            self.sidebar.finish_config_editor()
        self.assertEqual(self.sidebar.menu, "refresh")
        self.assertEqual(self.sidebar.menu_message, "Shortcuts saved")
        self.assertEqual(self.sidebar.keymap, DEFAULT_KEYMAP)

    def test_tab_menu_navigation_preserves_layouts_and_returns_input_focus(self):
        first = self.model.tab
        self.model.add_tab()
        second = self.model.tab
        self.model.split("right")
        self.model.split("below")
        self.model.split("right")
        self.display.focused_leaf.return_value = second["focus"]
        layouts = [copy.deepcopy(tab["tree"]) for tab in (first, second)]
        for label, target in (("Previous tab", first), ("Next tab", second)):
            self.sidebar.open_menu("tab")
            dict(self.sidebar._options({}))[label]()
            self.assertIs(self.model.tab, target)
            self.assertIsNone(self.sidebar.menu)
        self.assertEqual([tab["tree"] for tab in (first, second)], layouts)

        original = second["focus"]
        self.display.select.side_effect = lambda leaf: setattr(
            self.display.focused_leaf, "return_value", leaf
        )
        for focused in (False, True):
            self.model.state["focus"] = focused
            self.sidebar.open_menu("tab")
            dict(self.sidebar._options({}))["Previous pane"]()
            self.assertNotEqual(second["focus"], original)
            self.assertIsNone(self.sidebar.menu)
            self.sidebar.open_menu("tab")
            dict(self.sidebar._options({}))["Next pane"]()
            self.assertEqual(second["focus"], original)
            self.assertIsNone(self.sidebar.menu)
            self.assertEqual(self.model.state["focus"], focused)
        self.display.shells.close.assert_not_called()

    def test_tab_menu_shortcut_hints_follow_effective_map_and_fit_without_clipping(self):
        self.sidebar.keymap = Keymap.from_dict(
            {
                "prefix": "C-b",
                "bindings": {"previous-tab": ["F2"], "next-tab": []},
                "direct": {"previous-tab": ["super+alt+f2"], "next-tab": []},
            }
        )
        self.sidebar.open_menu("tab")
        self.sidebar.draw()
        self.sidebar.move_selection(1)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.tab_shortcut_hint(26), "Ctrl-b, then F2")
        self.assertEqual(self.sidebar.tab_shortcut_hint(16), "View shortcuts")
        self.sidebar.shortcut_hints = "command"
        self.assertEqual(self.sidebar.tab_shortcut_hint(26), "⌘⌥F2")
        self.sidebar.move_selection(1)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.tab_shortcut_hint(26), "No direct key")
        self.sidebar.shortcut_hints = "prefix"
        self.assertEqual(self.sidebar.tab_shortcut_hint(26), "No prefix key")
        self.sidebar.move_selection(1)  # Split right has two default aliases.
        self.sidebar.draw()
        self.assertEqual(self.sidebar.tab_shortcut_hint(16), "View shortcuts")

    def test_navigation_menu_hit_targets_follow_short_narrow_scrolling(self):
        self.screen.getmaxyx.return_value = (16, 20)
        self.sidebar.open_menu("tab")
        self.sidebar.draw()
        for _ in self.sidebar._options({}):
            if self.sidebar.selection.entry().label == "Next pane":
                break
            self.sidebar.move_selection(1)
            self.sidebar.draw()
        else:
            self.fail("Next pane was not reachable by keyboard")
        with patch.object(self.sidebar, "menu_next_pane") as advance:
            self.sidebar.last_frame = None
            self.sidebar.draw()
            row = next(
                call.args[0]
                for call in reversed(self.screen.addnstr.call_args_list)
                if call.args[2].strip() == "Next pane"
            )
            self.assertLess(row, self.sidebar.size()[0] - 3)
            self.mouse(3, row, curses.BUTTON1_PRESSED)
        advance.assert_called_once_with()

    def test_the_heading_chevron_is_the_one_place_workspaces_are_switched_and_made(self):
        first = self.model.space
        self.model.add_workspace("Second")
        second = self.model.space
        self.model.state["selected"] = first["id"]
        self.sidebar.draw()
        labels = [call.args[2].strip() for call in self.screen.addnstr.call_args_list]
        self.assertNotIn("workspaces", labels)
        # Every cell of the chevron button opens the chooser.
        for x in (23, 24):
            self.sidebar.draw()
            self.mouse(x, 0, curses.BUTTON1_PRESSED)
            self.assertEqual(self.sidebar.menu, "workspace")
            self.sidebar.close_menu()
        self.mouse(24, 0, curses.BUTTON1_PRESSED)
        dict(self.sidebar._options({}))["Switch workspace"]()
        dict(self.sidebar._options({}))["Second"]()
        self.assertEqual(self.model.space["id"], second["id"])
        self.sidebar.draw()
        self.mouse(24, 0, curses.BUTTON1_PRESSED)
        dict(self.sidebar._options({}))["New workspace"]()
        self.assertEqual((self.sidebar.menu, self.sidebar.pending), ("name", "new-workspace"))

    def test_workspace_shortcuts_still_cycle_at_narrow_width(self):
        self.screen.getmaxyx.return_value = (38, 22)
        for index in range(1, 12):
            self.model.add_workspace(f"Workspace {index + 1}")
        spaces = self.model.state["workspaces"]
        self.model.state["selected"] = spaces[0]["id"]
        for expected in [*spaces[1:], spaces[0]]:
            self.sidebar.draw()
            self.sidebar.action("next-workspace")
            self.assertEqual(self.model.space["id"], expected["id"])
        self.sidebar.action("previous-workspace")
        self.assertEqual(self.model.space["id"], spaces[-1]["id"])
        self.sidebar.action("select-workspace-12")
        self.assertEqual(self.model.space["id"], spaces[11]["id"])
        self.sidebar.draw()
        heading = next(
            call.args[2]
            for call in self.screen.addnstr.call_args_list
            if call.args[0] == 0 and call.args[1] == 1
        )
        self.assertTrue(heading.startswith("Workspace 1"))

    def test_indexed_tab_selection_stays_visible_with_shorter_footer(self):
        self.screen.getmaxyx.return_value = (30, 28)
        for index in range(12):
            self.model.add_tab(f"Tab {index + 2}")
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        self.sidebar.action("select-tab-9")
        self.sidebar.draw()
        labels = [call.args[2].strip() for call in self.screen.addnstr.call_args_list]
        self.assertTrue(any("9 Tab 9" in label for label in labels))
        # 28 rows inside the outline, less the heading, its blank row, the
        # label and the five-row footer; this source reports no roster.
        self.assertEqual(self.sidebar.tab_capacity(), 20)
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
        # Every tab is one row. The selected row carries its count and then
        # the ⋯ menu on the right inset; a resting row's count sits one cell in
        # from the interior's edge. No row repeats what the panes hold.
        self.assertEqual(cells[3, 21], "2")
        self.assertEqual(cells[3, 23], "⋯")
        self.assertTrue(cells[4, 0].endswith("…"))
        self.assertEqual(cells[4, 24], "1")
        self.assertIn("3 Third", cells[5, 0])
        self.assertFalse(any(text in {"shells", "Shell", "Empty"} for text in cells.values()))
        # Counts and the right edge belong to the tab, not an adjacent row.
        self.mouse(25, 4, curses.BUTTON1_PRESSED)
        self.assertEqual(self.model.tab["id"], second["id"])
        self.sidebar.draw()
        self.mouse(23, 4, curses.BUTTON1_PRESSED)  # The ⋯ moved with the selection.
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
        # Configure… sits two rows above the icon row, inside the bounded footer.
        self.mouse(3, 14, curses.BUTTON1_PRESSED)
        self.assertEqual(self.sidebar.menu, "configure")

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

    def drawn(self):
        return [
            (call.args[0], call.args[1], call.args[2]) for call in self.screen.addnstr.mock_calls
        ]

    def test_refresh_confirms_before_replacing_and_cancelling_changes_nothing(self):
        self.sidebar.action("refresh-viewer")
        self.assertEqual(self.sidebar.menu, "refresh")
        self.sidebar.draw()
        rows = self.drawn()
        self.assertIn((2, 1, "Refresh viewer"), rows)
        # The note wraps inside the panel's interior; it starts on the row
        # under the title.
        self.assertTrue(
            any(row[:2] == (3, 1) and row[2].startswith("Restarts this") for row in rows)
        )
        option = next(row for row in rows if row[2].startswith("Refresh viewer now"))
        self.assertIn(
            (option[0], 1, option[1] + len(option[2])), [hit[:3] for hit in self.sidebar.hits]
        )
        self.assertTrue(any(row[1] == 1 and row[2].startswith("Enter refresh") for row in rows))
        # Back leaves the viewer running with nothing requested.
        dict(self.sidebar._options({}))
        self.sidebar.input("\x1b")
        self.assertIsNone(self.sidebar.menu)
        self.assertTrue(self.sidebar.running)
        self.assertFalse(self.relaunch.requested)
        self.assertEqual(self.relaunch.submitted, [])

    def test_confirmed_refresh_saves_first_and_requests_one_replacement(self):
        self.model.add_tab("Second tab")
        self.sidebar.action("refresh-viewer")
        confirm = dict(self.sidebar._options({}))["Refresh viewer now"]
        # Enter confirms too, so the action never needs the mouse.
        self.sidebar.draw()
        self.sidebar.input("\n")
        self.store.save.assert_called_with(self.model)
        self.assertFalse(self.sidebar.running)
        self.assertEqual(
            self.relaunch.submitted,
            [
                {
                    "workspace": self.model.space["id"],
                    "tab": self.model.tab["id"],
                    "leaf": self.model.tab["focus"],
                    "focus": False,
                }
            ],
        )
        # A duplicate click on the same confirmation is not a second viewer.
        confirm()
        self.assertEqual(len(self.relaunch.submitted), 1)
        self.assertEqual(self.sidebar.message, "Refresh already requested")

    def test_refresh_keeps_the_viewer_when_saving_or_validation_fails(self):
        self.store.save.side_effect = LayoutConflict("Layout changed in another window")
        self.sidebar.action("refresh-viewer")
        dict(self.sidebar._options({}))["Refresh viewer now"]()
        self.assertTrue(self.sidebar.running)
        self.assertFalse(self.relaunch.requested)
        self.assertIn("another window", self.sidebar.message)
        self.assertIsNone(self.sidebar.menu)

        # The confirmation itself is what checks, so a file broken while the
        # menu was open still cannot close this window.
        self.store.save.side_effect = None
        self.sidebar.action("refresh-viewer")
        confirm = dict(self.sidebar._options({}))["Refresh viewer now"]
        self.relaunch.problem = "keymap /tmp/keymap.toml: unsupported tmux key 'zz'"
        confirm()
        self.assertTrue(self.sidebar.running)
        self.assertEqual(self.relaunch.submitted, [])
        self.assertIn("unsupported tmux key", self.sidebar.message)

    def test_a_broken_launcher_is_reported_on_confirmation_without_closing(self):
        self.relaunch.problem = "Cannot reopen: /checkout/run is missing"
        self.sidebar.action("refresh-viewer")
        self.assertEqual(self.sidebar.menu, "refresh")
        # Opening the menu never probes; the confirmation does, exactly once.
        self.assertIsNone(self.sidebar.refresh_problem)
        self.sidebar.draw()
        self.sidebar.input("\n")
        self.assertTrue(self.sidebar.running)
        self.assertEqual(self.relaunch.submitted, [])
        self.sidebar.draw()
        rows = [text for _row, _x, text in self.drawn()]
        self.assertIn("/checkout/run is missing", rows)
        self.assertIn("Nothing was closed.", rows)
        self.assertNotIn("Refresh viewer now", dict(self.sidebar._options({})))

    def test_a_window_with_fixed_terminal_keys_shows_the_command_to_reopen_it(self):
        self.relaunch.manual_reopen = True
        self.relaunch.problem = "This window is reopened by hand"
        self.relaunch.lines = ("This window's keys were fixed.", "Start it again with:")
        self.relaunch.command = "/checkout/ghostty --data-dir '/lib rary'"
        self.sidebar.action("refresh-viewer")
        options = list(dict(self.sidebar._options({})))
        self.assertNotIn("Refresh viewer now", options)
        self.assertEqual(options, [])
        self.assertEqual(" ".join(self.sidebar.command_lines()), self.relaunch.command)
        self.sidebar.draw()
        rows = [text for _row, _x, text in self.drawn()]
        self.assertIn("This window's keys were", rows)
        self.assertIn("Esc close", rows)
        # The instruction rows are inert: nothing is torn down by reading them.
        for _label, action in self.sidebar._options({}):
            action()
        self.assertTrue(self.sidebar.running)
        self.assertEqual(self.relaunch.submitted, [])

    def test_reopen_command_is_read_only_and_scrolls_without_selecting_fragments(self):
        self.relaunch.manual_reopen = True
        self.relaunch.problem = "Reopen this terminal"
        self.relaunch.command = "run " + " ".join(f"segment{i:02d}" for i in range(80))
        self.sidebar.action("refresh-viewer")
        self.screen.getmaxyx.return_value = (20, 28)
        self.sidebar.draw()
        first = self.sidebar.command_rows(self.sidebar.menu_rows()[1])
        self.assertIn("segment00", " ".join(first))
        self.assertIsNone(self.sidebar.selection.entry())
        self.assertEqual(self.sidebar._options({}), [])
        self.sidebar.input(curses.KEY_END)
        self.sidebar.draw()
        last = self.sidebar.command_rows(self.sidebar.menu_rows()[1])
        self.assertIn("segment79", " ".join(last))
        self.assertNotEqual(first, last)
        self.sidebar.input("\n")
        self.assertTrue(self.sidebar.running)
        self.assertEqual(self.relaunch.submitted, [])
        self.sidebar.input(curses.KEY_HOME)
        self.sidebar.draw()
        self.assertEqual(self.sidebar.command_rows(self.sidebar.menu_rows()[1]), first)
        # No text row is a button; only Back and the scroll controls are hit-tested.
        self.assertEqual(len(self.sidebar.hits), 3)

    def test_refresh_defers_to_an_open_name_edit_and_to_an_unlaunched_viewer(self):
        self.sidebar.draw()
        self.sidebar.click_name(self.model.tab["id"], 3, 2, double=True)
        self.assertIsNotNone(self.sidebar.inline_editor)
        self.sidebar.action("refresh-viewer")
        self.assertIsNotNone(self.sidebar.inline_editor)
        self.assertEqual(self.sidebar.menu, "inline-name")
        self.assertFalse(self.relaunch.requested)
        self.assertEqual(self.sidebar.message, "Finish or cancel the name edit first")
        self.sidebar.input("\x1b")

        self.sidebar.relaunch = None
        self.sidebar.action("refresh-viewer")
        self.assertEqual(self.sidebar._options({}), [])
        self.sidebar.input("\n")
        self.assertTrue(self.sidebar.running)
        self.assertEqual(self.sidebar.refresh_problem, "Refresh is unavailable here")

    def test_refresh_refuses_while_a_name_menu_is_open_and_says_so_there(self):
        self.sidebar.action("rename-tab")
        self.assertEqual(self.sidebar.menu, "name")
        self.sidebar.action("refresh-viewer")
        self.assertEqual(self.sidebar.menu, "name")
        self.assertFalse(self.relaunch.requested)
        self.sidebar.draw()
        self.assertIn(
            "Finish or cancel the name edit first", [text for _r, _x, text in self.drawn()]
        )

    def test_gestures_queued_behind_a_confirmed_refresh_are_released_not_applied(self):
        self.model.add_tab("Second tab")
        actions = FakeActions(["close-tab", "new-tab", "quit"])
        self.sidebar.actions = actions
        self.sidebar.action("refresh-viewer")
        self.sidebar.draw()
        self.sidebar.input("\n")
        self.assertFalse(self.sidebar.running)
        self.assertEqual(len(self.relaunch.submitted), 1)
        tabs = copy.deepcopy(self.model.space["tabs"])

        self.assertFalse(self.sidebar.apply_pending())
        self.assertEqual(self.model.space["tabs"], tabs)
        self.display.shells.close.assert_not_called()
        # Every waiting shortcut is still released, so none of them hangs.
        self.assertEqual(actions.acknowledged, ["close-tab", "new-tab", "quit"])

    def test_queued_gestures_before_a_refresh_still_apply(self):
        actions = FakeActions(["new-tab"])
        self.sidebar.actions = actions
        self.assertTrue(self.sidebar.apply_pending())
        self.assertEqual(len(self.model.space["tabs"]), 2)
        self.assertEqual(actions.acknowledged, ["new-tab"])


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

    def active(self):
        return self.sidebar.options[self.sidebar.selected][0]

    def painted_rows(self, start=4):
        """Menu row labels the next frame actually paints, ignoring earlier frames."""
        self.screen.addnstr.reset_mock()
        self.sidebar.last_frame = None
        self.sidebar.draw()
        height = self.screen.getmaxyx()[0]
        return [
            call.args[2].strip()
            for call in self.screen.addnstr.call_args_list
            if call.args[1] == 1 and start <= call.args[0] < height - 3
        ]

    def overflowing_workspaces(self, count=20):
        """Open the workspace chooser on a list taller than its window."""
        self.screen.getmaxyx.return_value = (21, 28)
        for index in range(count - 1):
            self.model.add_workspace(f"Space {index + 2}")
        self.model.state["selected"] = self.model.state["workspaces"][0]["id"]
        self.sidebar.action("workspaces")
        names = [space["name"] for space in self.model.state["workspaces"]]
        self.assertEqual(self.labels(), names)
        return names

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
        self.screen.getmaxyx.return_value = (18, 28)
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
        self.assertIn("Enlarge terminal", drawn)
        self.assertIn("Detach", drawn)
        self.assertNotIn("unseen", drawn)
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

    def test_end_and_home_reach_both_edges_of_an_overflowing_chooser(self):
        names = self.overflowing_workspaces()
        rows = self.painted_rows()
        self.assertEqual(len(rows), 13)
        self.assertNotIn(names[-1], rows)

        self.sidebar.input(curses.KEY_END)
        rows = self.painted_rows()
        self.assertEqual(self.active(), names[-1])
        self.assertIn(names[-1], rows)

        self.sidebar.input(curses.KEY_HOME)
        rows = self.painted_rows()
        self.assertEqual((self.active(), self.sidebar.offset), (names[0], 0))
        self.assertIn(names[0], rows)

    def test_page_keys_move_a_whole_window_and_keep_the_selection_visible(self):
        names = self.overflowing_workspaces()
        page = self.sidebar.size()[0] - 9

        self.sidebar.input(curses.KEY_NPAGE)
        rows = self.painted_rows()
        self.assertEqual(self.active(), names[page])
        self.assertIn(names[page], rows)

        self.sidebar.input(curses.KEY_NPAGE)
        rows = self.painted_rows()
        self.assertEqual(self.active(), names[-1])
        self.assertIn(names[-1], rows)

        self.sidebar.input(curses.KEY_PPAGE)
        rows = self.painted_rows()
        self.assertEqual(self.active(), names[len(names) - 1 - page])
        self.assertIn(self.active(), rows)

        self.sidebar.input(curses.KEY_PPAGE)
        rows = self.painted_rows()
        self.assertEqual((self.active(), self.sidebar.offset), (names[0], 0))
        self.assertIn(names[0], rows)

    def test_a_row_reached_by_page_or_end_is_the_one_enter_activates(self):
        names = self.overflowing_workspaces()
        self.sidebar.input(curses.KEY_END)
        self.assertIn(names[-1], self.painted_rows())
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], names[-1])
        self.assertIsNone(self.sidebar.menu)


if __name__ == "__main__":
    unittest.main()


class ThemeMenuTests(unittest.TestCase):
    """The compact color editor reached from the sidebar, by click or by key."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "theme.toml"
        self.model = Model.initial()
        self.screen = Mock()
        # The panel draws its interior into a subwindow; the tests read one mock.
        self.screen.derwin.return_value = self.screen
        self.screen.getmaxyx.return_value = (38, 28)
        self.store = Mock()
        source = Mock(socket="/unused/source.sock", persistent_socket=True)
        source.snapshot.return_value = ({}, "")
        self.display = Mock(sidebar="%0", small=False, keymap=DEFAULT_KEYMAP)
        self.display.focused_leaf.return_value = self.model.tab["focus"]
        self.sidebar = Sidebar(
            self.screen,
            self.model,
            self.store,
            source,
            self.display,
            Mock(),
            theme_path=self.path,
            terminal_colors=256,
        )
        for name, result in (
            ("color_pair", 0),
            ("init_pair", None),
            ("start_color", None),
            ("use_default_colors", None),
        ):
            patcher = patch(f"tmux_workspaces.sidebar.curses.{name}", return_value=result)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        colors = patch("tmux_workspaces.sidebar.curses.COLORS", 256, create=True)
        colors.start()
        self.addCleanup(colors.stop)
        self.sidebar.setup_theme()

    def test_startup_without_a_config_keeps_the_shipped_pairs(self):
        self.assertEqual(self.sidebar.theme, DEFAULT_THEME)
        self.assertEqual(self.sidebar.colors, 256)
        self.assertEqual(
            [call.args for call in self.init_pair.call_args_list],
            [(1, 16, 17), (2, 16, 18), (3, 19, 17), (4, 20, 17), (5, 21, 22)],
        )
        self.assertEqual(self.sidebar.message, "")

    def test_a_smaller_terminal_palette_bounds_the_outer_client_snapshot(self):
        # The transported outer capability cannot exceed what init_pair accepts.
        with patch("tmux_workspaces.sidebar.curses.COLORS", 8, create=True):
            self.sidebar.setup_theme()
        self.assertEqual(self.sidebar.colors, 8)
        self.assertEqual(
            [call.args for call in self.init_pair.call_args_list[-5:]],
            [(1, 7, 0), (2, 7, 4), (3, 6, 0), (4, 7, 0), (5, 7, 0)],
        )

    def test_the_sidebar_base_takes_the_normal_role(self):
        configured = DEFAULT_THEME.with_role("normal", background=["blue"])
        self.path.write_text(configured.to_toml())
        self.sidebar.setup_theme()
        self.assertEqual(self.init_pair.call_args_list[-5].args, (1, 16, 4))
        self.screen.bkgdset.assert_called_with(" ", self.sidebar.style("normal"))
        self.screen.getmaxyx.return_value = (10, 10)
        self.sidebar.draw()
        small = [call.args for call in self.screen.addnstr.call_args_list]
        notice = next(call for call in small if call[2] == "Enlarge terminal")
        self.assertEqual(notice[4], self.sidebar.style("normal"))

    def test_unreadable_config_reports_it_and_keeps_working_colors(self):
        self.path.write_text("[active]\nforeground = '#8ab4f'\n")
        self.sidebar.setup_theme()
        self.assertEqual(self.sidebar.theme, DEFAULT_THEME)
        self.assertTrue(self.sidebar.message.startswith("Error: active.foreground"))

    def test_the_workspace_header_carries_the_normal_role(self):
        self.sidebar.draw()
        header = next(
            call.args
            for call in self.screen.addnstr.call_args_list
            if call.args[2].startswith(self.model.space["name"])
        )
        self.assertEqual(header[4], self.sidebar.style("normal") | curses.A_BOLD)

    def styles_for(self, prefix):
        return [
            call.args[4]
            for call in self.screen.addnstr.call_args_list
            if call.args[2].startswith(prefix)
        ]

    def test_a_startup_theme_failure_is_bold_in_the_status_row(self):
        self.path.write_text("[active]\nforeground = '#8ab4f'\n")
        self.sidebar.setup_theme()
        self.sidebar.draw()
        rows = self.styles_for("Error: ")
        self.assertTrue(rows and all(style & curses.A_BOLD for style in rows))
        self.sidebar.message = ""
        self.screen.addnstr.reset_mock()
        self.sidebar.draw()
        # Saving is quiet: with nothing to say, no status row is drawn at all.
        self.assertFalse(self.styles_for("Error: "))
        self.assertFalse(self.styles_for("Layouts saved"))
