import curses
import unittest
from unittest.mock import Mock, patch

from tmux_workspaces.keymap import ACTION_LABELS, DEFAULT_KEYMAP, Keymap
from tmux_workspaces.shortcut_reference import ShortcutReference, reference_rows


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.screen = Mock()
        self.screen.getmaxyx.return_value = (24, 100)
        self.reference = ShortcutReference(self.screen, DEFAULT_KEYMAP)

    def test_all_effective_actions_and_custom_aliases_are_readable_at_both_widths(self):
        keymap = Keymap.from_dict(
            {"bindings": {"new-tab": ["F8", "F9"], "close-pane": []}, "direct": {"close-pane": []}}
        )
        for width in (36, 96):
            rows = reference_rows(keymap, width)
            text = "\n".join(line for line, _ in rows)
            self.assertTrue(all(len(line) <= width for line, _ in rows))
            self.assertIn("F8 / F9", text)
            self.assertNotIn("Close pane", text)
            for action, label in ACTION_LABELS.items():
                if keymap.bindings[action] or keymap.direct[action]:
                    self.assertIn(label, text)

    def test_paste_typing_and_action_sequences_cannot_edit_or_close(self):
        before = self.reference.keymap.to_dict()
        for char in "\x1b[200~\x03\x13\x07tPASTE\x1b[201~\x1b[9001~\r\n\x13abc":
            self.reference.feed(char)
        self.assertFalse(self.reference.done)
        self.assertEqual(self.reference.keymap.to_dict(), before)
        self.assertEqual(self.reference.offset, 0)

    def test_scroll_resize_and_mouse_close_keep_the_reference_read_only(self):
        self.reference.draw()
        self.reference.key(curses.KEY_NPAGE)
        self.assertGreater(self.reference.offset, 0)
        self.reference.key(curses.KEY_END)
        self.screen.getmaxyx.return_value = (12, 40)
        self.reference.draw()
        self.reference.key(curses.KEY_HOME)
        self.assertEqual(self.reference.offset, 0)
        with patch(
            "tmux_workspaces.shortcut_reference.curses.getmouse",
            return_value=(0, 2, 10, 0, curses.BUTTON1_PRESSED),
        ):
            self.reference.key(curses.KEY_MOUSE)
        self.assertTrue(self.reference.done)

    def test_decoded_keys_and_delayed_escape_stay_inside_an_active_paste(self):
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10):
            for char in "\x1b[200~text\x1b":
                self.reference.feed(char)
            self.reference.feed(curses.KEY_F10)
            self.reference.feed(curses.KEY_F10)
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10.5):
            self.reference.idle()
        self.assertFalse(self.reference.done)
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=13):
            self.reference.idle()
            self.reference.feed("\x03")
            self.assertFalse(self.reference.done)
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=14):
            self.reference.idle()
        self.assertTrue(self.reference.done)

    def test_small_terminal_clears_mouse_close_hit_and_escape_still_closes(self):
        self.reference.draw()
        self.screen.getmaxyx.return_value = (8, 30)
        self.reference.draw()
        self.assertIsNone(self.reference.close_hit)
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=10):
            self.reference.feed("\x1b")
        with patch("tmux_workspaces.shortcut_reference.time.monotonic", return_value=11):
            self.reference.idle()
        self.assertTrue(self.reference.done)
