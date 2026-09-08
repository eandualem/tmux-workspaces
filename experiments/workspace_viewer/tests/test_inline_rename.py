import copy
import curses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Store
from name_editor import NameEditor, cells
from sidebar import Sidebar


class InlineRenameTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tw-rename-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = Store(self.root)
        self.addCleanup(self.store.close)
        self.model = self.store.load()
        self.model.tab["name"] = "Original"
        self.store.save(self.model)
        self.display = Mock(sidebar="%0", small=False)
        self.display.focused_leaf.side_effect = lambda: (
            self.model.tab["focus"] if self.model.tab else None
        )
        screen = Mock()
        screen.getmaxyx.return_value = (38, 28)
        source = Mock(socket="/unused", demo=None)
        source.snapshot.return_value = ({}, "")
        self.sidebar = Sidebar(screen, self.model, self.store, source, self.display, Mock())
        for target in ("sidebar.curses.color_pair", "sidebar.curses.curs_set"):
            patcher = patch(target, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sidebar.draw()

    def tap(self, when, x=6, row=2):
        with patch("sidebar.time.monotonic", return_value=when):
            self.sidebar.mouse(x, row, curses.BUTTON1_PRESSED)
        self.sidebar.mouse(x, row, curses.BUTTON1_RELEASED)

    def begin(self):
        self.tap(10)
        self.tap(10.2)
        self.assertEqual(self.sidebar.menu, "inline-name")
        self.sidebar.draw()

    def peer(self):
        store = Store(self.root)
        self.addCleanup(store.close)
        return store, store.load()

    def test_double_click_edits_in_place_and_saves_without_changing_layout(self):
        before = copy.deepcopy(self.model.tab)
        self.tap(10)
        self.assertIsNone(self.sidebar.inline_editor)
        self.display.render.assert_not_called()
        self.tap(10.2)
        self.sidebar.draw()
        self.display.render.assert_not_called()
        rendered = self.sidebar.screen.addnstr.call_args_list
        self.assertTrue(
            any(c.args[:2] == (2, 4) and c.args[2].startswith("Original") for c in rendered)
        )
        for char in "Edited":
            self.sidebar.input(char)
        self.assertEqual(self.model.tab["name"], "Original")
        self.sidebar.input("\n")
        self.assertEqual(self.model.tab, before | {"name": "Edited"})
        self.assertEqual(self.store.load().tab["name"], "Edited")
        self.display.shells.close.assert_not_called()

    def test_inactive_double_click_only_selects_and_slow_clicks_do_not_rename(self):
        first = self.model.tab
        self.model.add_tab("Second")
        self.store.save(self.model)
        self.sidebar.draw()
        self.tap(10)
        self.assertEqual(self.model.tab["id"], first["id"])
        self.tap(10.2)
        self.assertIsNone(self.sidebar.inline_editor)
        self.tap(11)
        self.assertIsNone(self.sidebar.inline_editor)
        self.tap(11.2)
        self.assertIsNotNone(self.sidebar.inline_editor)

    def test_double_click_count_or_detail_never_opens_editor(self):
        self.tap(10, x=25)
        self.tap(10.2, x=25)
        self.sidebar.draw()
        self.tap(11, row=3)
        self.tap(11.2, row=3)
        self.assertIsNone(self.sidebar.inline_editor)

    def test_escape_and_outside_click_discard_draft(self):
        self.begin()
        self.sidebar.input("X")
        self.sidebar.input("\x1b")
        self.assertEqual(self.model.tab["name"], "Original")
        self.assertIsNone(self.sidebar.inline_editor)
        self.display.select.assert_called_with(self.model.tab["focus"])
        self.sidebar.draw()
        self.begin()
        self.sidebar.input("Y")
        self.sidebar.mouse(24, 0, curses.BUTTON1_PRESSED)
        self.assertIsNone(self.sidebar.inline_editor)
        self.assertEqual(self.model.space["tabs"][0]["name"], "Original")
        self.assertEqual(len(self.model.space["tabs"]), 2)

    def test_empty_name_keeps_editor_and_shortcut_cancels_draft(self):
        self.begin()
        self.sidebar.input("\x15")
        self.sidebar.input("\n")
        self.assertEqual(self.sidebar.menu, "inline-name")
        self.assertEqual(self.model.tab["name"], "Original")
        self.sidebar.action("rename-tab")
        self.assertIsNone(self.sidebar.inline_editor)
        self.assertEqual((self.sidebar.menu, self.sidebar.query), ("name", "Original"))

    def test_peer_rename_is_not_overwritten_by_open_editor(self):
        self.begin()
        peer, model = self.peer()
        model.tab["name"] = "From another window"
        peer.save(model)
        self.sidebar.input("X")
        self.sidebar.input("\n")
        self.assertEqual(self.model.tab["name"], "From another window")
        self.assertEqual(self.store.load().tab["name"], "From another window")
        self.assertIsNone(self.sidebar.inline_editor)
        self.display.tmux.run.assert_called_with("select-pane", "-t", "%0")
        self.assertIn("Tab changed", self.sidebar.message)

    def test_peer_deletion_cannot_rename_surviving_tab(self):
        self.begin()
        peer, model = self.peer()
        model.close_tab()
        model.add_tab("Survivor")
        peer.save(model)
        self.sidebar.input("X")
        self.sidebar.input("\n")
        self.assertEqual(self.model.tab["name"], "Survivor")
        self.assertIn("Tab changed", self.sidebar.message)
        self.display.shells.close.assert_not_called()

    def begin_workspace(self):
        self.tap(10, row=0)
        self.assertIsNone(self.sidebar.inline_editor)
        self.tap(10.2, row=0)
        self.assertEqual(self.sidebar.menu, "inline-name")
        self.assertIsNone(self.sidebar.inline_target[1])
        self.sidebar.draw()

    def test_workspace_header_edits_its_name_and_keeps_plus_for_new_tabs(self):
        tabs = copy.deepcopy(self.model.space["tabs"])
        self.begin_workspace()
        self.assertTrue(
            any(
                c.args[:2] == (0, 1) and c.args[2].startswith(self.model.space["name"])
                for c in self.sidebar.screen.addnstr.call_args_list
            )
        )
        for char in "Development":
            self.sidebar.input(char)
        self.assertNotEqual(self.model.space["name"], "Development")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "Development")
        self.assertEqual(self.model.space["tabs"], tabs)
        self.assertEqual(self.store.load().space["name"], "Development")
        self.sidebar.draw()
        self.sidebar.mouse(24, 0, curses.BUTTON1_PRESSED)
        self.assertEqual(len(self.model.space["tabs"]), 2)
        self.assertIsNone(self.sidebar.inline_editor)
        self.display.shells.close.assert_not_called()

    def test_empty_workspace_can_be_renamed_and_cancelled(self):
        self.model.close_tab()
        self.store.save(self.model)
        self.sidebar.draw()
        original = self.model.space["name"]
        self.begin_workspace()
        self.sidebar.input("\x15")
        self.sidebar.input("\n")
        self.assertEqual(self.sidebar.message, "Enter a workspace name")
        self.sidebar.input("X")
        self.sidebar.input("\x1b")
        self.assertEqual(self.model.space["name"], original)
        self.sidebar.draw()
        self.begin_workspace()
        self.sidebar.input("New empty")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "New empty")
        self.assertEqual(self.model.space["tabs"], [])

    def test_peer_workspace_rename_or_deletion_cannot_overwrite_survivor(self):
        self.begin_workspace()
        peer, model = self.peer()
        model.space["name"] = "Peer name"
        peer.save(model)
        self.sidebar.input("X")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "Peer name")
        self.assertEqual(self.sidebar.message, "Workspace changed; rename again")
        self.sidebar.draw()
        self.begin_workspace()
        peer, model = self.peer()
        model.add_workspace("Survivor")
        model.state["workspaces"].pop(0)
        peer.save(model)
        self.sidebar.input("X")
        self.sidebar.input("\n")
        self.assertEqual(self.model.space["name"], "Survivor")
        self.display.tmux.run.assert_called_with("select-pane", "-t", "%0")
        self.display.shells.close.assert_not_called()

    def test_clicks_on_different_names_do_not_combine_into_double_click(self):
        self.tap(10)
        self.tap(10.2, row=0)
        self.assertIsNone(self.sidebar.inline_editor)
        self.tap(10.4, row=0)
        self.assertIsNone(self.sidebar.inline_target[1])
        self.sidebar.draw()
        self.sidebar.mouse(6, 0, curses.BUTTON1_PRESSED)
        self.sidebar.input("!")
        self.assertIsNone(self.sidebar.inline_target[1])
        self.assertIn("!", self.sidebar.inline_editor.value)

    def test_name_editor_selection_cursor_delete_and_wide_text(self):
        editor = NameEditor("Original")
        editor.key(curses.KEY_RIGHT)
        editor.key("!")
        self.assertEqual(editor.value, "Original!")
        editor.key(curses.KEY_HOME)
        editor.key(curses.KEY_DC)
        editor.key("o")
        self.assertEqual(editor.value, "original!")
        editor = NameEditor("A界éB")
        editor.click(3, 5)
        self.assertEqual(editor.cursor, 2)
        editor.key("x")
        self.assertEqual(editor.value, "A界xéB")
        editor.key(curses.KEY_END)
        text, cursor = editor.viewport(4)
        self.assertLessEqual(cells(text), 4)
        self.assertLess(cursor, 4)
        editor.key("\x15")
        editor.key("N")
        self.assertEqual(editor.value, "N")
        editor = NameEditor("x" * 80)
        editor.key(curses.KEY_END)
        editor.key("y")
        self.assertEqual(len(editor.value), 80)
        editor.key(curses.KEY_BACKSPACE)
        editor.key("y")
        self.assertTrue(editor.value.endswith("y"))


if __name__ == "__main__":
    unittest.main()
