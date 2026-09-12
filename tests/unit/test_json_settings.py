import curses
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from tmux_workspaces.config_editor import Editor, clip
from tmux_workspaces.json_settings import SettingsDraft
from tmux_workspaces.keymap import DEFAULT_KEYMAP, parse_keymap
from tmux_workspaces.text_buffer import TextBuffer
from tmux_workspaces.theme import DEFAULT_THEME, parse_theme


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_json_save_uses_existing_keymap_validation_and_toml_format(self):
        path = self.root / "keymap.toml"
        draft = SettingsDraft("shortcuts", path)
        self.assertTrue(draft.save('{"prefix":"C-a","bindings":{"new-tab":["u"]}}'))
        value = parse_keymap(path.read_bytes())
        self.assertEqual(value.prefix, "C-a")
        self.assertEqual(value.bindings["new-tab"], ("u",))

    def test_invalid_json_and_domain_values_never_change_the_working_file(self):
        for kind, default, bad in (
            ("shortcuts", DEFAULT_KEYMAP, '{"bindings":{"new-tab":["r"]}}'),
            ("colors", DEFAULT_THEME, '{"panel":"bad-color"}'),
        ):
            path = self.root / (kind + ".toml")
            original = default.to_toml().encode()
            path.write_bytes(original)
            draft = SettingsDraft(kind, path)
            for text in (
                "{",
                "[]",
                '{"prefix":"C-g","prefix":"C-a"}',
                '{"x":NaN}',
                "{" * 2000,
                bad,
            ):
                self.assertFalse(draft.save(text))
                self.assertTrue(draft.message)
                self.assertEqual(path.read_bytes(), original)

    def test_comments_require_confirmation_and_a_concurrent_edit_is_preserved(self):
        path = self.root / "theme.toml"
        path.write_text('# my colors\npreset="paper"\n')
        draft = SettingsDraft("colors", path)
        self.assertFalse(draft.save('{"preset":"forest"}'))
        self.assertIn("Save again", draft.message)
        path.write_text('preset="mono"\n')
        self.assertFalse(draft.save('{"preset":"forest"}'))
        self.assertIn("changed", draft.message)
        self.assertEqual(parse_theme(path.read_bytes()).preset_name(), "mono")

    def test_conversion_confirmation_is_bound_to_the_current_draft(self):
        path = self.root / "theme.toml"
        path.write_text('# comment\npreset="paper"\n')
        draft = SettingsDraft("colors", path)
        self.assertFalse(draft.save('{"preset":"forest"}'))
        self.assertFalse(draft.save('{"preset":"mono"}'))
        self.assertTrue(draft.save('{"preset":"mono"}'))

    def test_symlink_and_readonly_save_keep_target(self):
        path = self.root / "real.toml"
        original = DEFAULT_THEME.to_toml()
        path.write_text(original)
        link = self.root / "theme.toml"
        link.symlink_to(path)
        for candidate in (link, path):
            path.chmod(0o400)
            draft = SettingsDraft("colors", candidate)
            draft.save('{"preset":"paper"}')
            self.assertFalse(draft.save('{"preset":"paper"}'))
            self.assertEqual(path.read_text(), original)
        path.chmod(0o600)

    def test_cancel_and_no_change_save_do_not_create_or_rewrite_files(self):
        path = self.root / "colors.toml"
        draft = SettingsDraft("colors", path)
        editor = Editor(Mock(), draft)
        editor.cancel()
        self.assertTrue(editor.done)
        self.assertFalse(path.exists())
        path.write_text(DEFAULT_THEME.to_toml())
        draft = SettingsDraft("colors", path)
        self.assertFalse(draft.save(draft.initial))
        self.assertFalse(draft.saved)

    def test_deep_existing_toml_opens_repair_draft(self):
        path = self.root / "theme.toml"
        text = "normal = " + "[" * 2000 + "0" + "]" * 2000
        path.write_text(text)
        draft = SettingsDraft("colors", path)
        self.assertTrue(draft.repair)
        self.assertEqual(path.read_text(), text)

    def test_editor_keeps_deep_json_and_large_integer_after_failed_save(self):
        draft = SettingsDraft("colors", self.root / "theme.toml")
        editor = Editor(Mock(), draft)
        for text in ("[" * 2000 + "0" + "]" * 2000, '{"panel":' + "9" * 5000 + "}"):
            editor.buffer.text = text
            editor.save()
            self.assertFalse(editor.done)
            self.assertEqual(editor.buffer.text, text)
            self.assertTrue(editor.message)


class BufferTests(unittest.TestCase):
    def test_multiline_navigation_delete_and_undo_redo(self):
        buffer = TextBuffer("abc\n  def\n")
        buffer.key(curses.KEY_DOWN)
        buffer.key(curses.KEY_END)
        buffer.key("\n")
        buffer.key("x")
        self.assertEqual(buffer.text, "abc\n  def\n  x\n")
        buffer.key("\x1a")
        self.assertEqual(buffer.text, "abc\n  def\n  \n")
        buffer.key("\x19")
        self.assertEqual(buffer.text, "abc\n  def\n  x\n")
        buffer.key(curses.KEY_BACKSPACE)
        buffer.key(curses.KEY_HOME)
        buffer.key(curses.KEY_DC)
        self.assertEqual(buffer.text, "abc\n  def\n \n")

    def test_selection_paste_is_atomic_and_overflow_keeps_selection(self):
        buffer = TextBuffer("old", limit=10)
        buffer.key("\x01")
        self.assertFalse(buffer.replace("x" * 11))
        self.assertEqual(buffer.text, "old")
        self.assertTrue(buffer.replace("new\ntext"))
        buffer.undo()
        self.assertEqual(buffer.text, "old")

    def test_scroll_mouse_and_wide_character_clipping(self):
        buffer = TextBuffer("one\n界abc\nthree\nfour")
        buffer.move_to(3, 4)
        buffer.viewport(2, 3)
        self.assertEqual(buffer.top, 2)
        buffer.move_to(1, 0)
        buffer.viewport(2, 10)
        buffer.click(0, 2)
        self.assertEqual(buffer.position(), (1, 1))
        self.assertEqual(clip("界abc", 1, 3), " ab")

    def test_bracketed_paste_and_direct_sequences_never_invoke_save(self):
        draft = Mock(initial="", file=Mock(limit=100), message="")
        screen = Mock()
        screen.getmaxyx.return_value = (30, 100)
        editor = Editor(screen, draft)
        for key in '\x1b[200~{"prefix":"C-g"}\x13\x1b[201~\x1b[9001~':
            editor.feed(key)
        self.assertEqual(json.loads(editor.buffer.text), {"prefix": "C-g"})
        draft.save.assert_not_called()
        editor.buffer.undo()
        self.assertEqual(editor.buffer.text, "")

    def test_dirty_cancel_requires_confirmation_and_editing_clears_it(self):
        draft = Mock(initial="{}", file=Mock(limit=100), message="")
        editor = Editor(Mock(), draft)
        editor.buffer.replace("x")
        editor.cancel()
        self.assertFalse(editor.done)
        self.assertTrue(editor.discard)
        editor.cancel()
        self.assertTrue(editor.done)
        draft.save.assert_not_called()

    def test_paste_and_format_invalidate_discard_confirmation(self):
        screen = Mock()
        screen.getmaxyx.return_value = (30, 100)
        draft = Mock(initial="{}", file=Mock(limit=100), message="")
        editor = Editor(screen, draft)
        editor.buffer.text = '{"panel":"red"}'
        editor.cancel()
        for key in "\x1b[200~ \x1b[201~":
            editor.feed(key)
        self.assertFalse(editor.discard)
        editor.cancel()
        self.assertFalse(editor.done)
        editor.key(curses.KEY_F4)
        self.assertFalse(editor.discard)
        editor.cancel()
        self.assertFalse(editor.done)

    def test_enter_and_tab_report_text_limit(self):
        buffer = TextBuffer("abc", limit=3)
        buffer.cursor = 3
        self.assertFalse(buffer.key("\n"))
        self.assertFalse(buffer.key("\t"))
        self.assertEqual(buffer.text, "abc")


if __name__ == "__main__":
    unittest.main()
